"""扒谱主流程：MP3/WAV -> Score。"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import audio as A
from .fingering import LH_FINGER_TEMPLATE, rh_fingering
from .model import (MAJOR_STEPS, Event, Measure, PIANO_HIGH, PIANO_LOW, Score,
                    key_signature_ascii, midi_to_name)

# 左手分解音型：音级偏移 / 八度偏移（与参考图完全一致）
FIG_DEGREE = [0, 4, 0, 2, 4, 2, 0, 4]
FIG_OCTAVE = [0, 0, 1, 1, 1, 1, 1, 0]
FIG_FINGER = [5, 2, 1, 2, 1, 2, 1, 2]

_DIATONIC = {0: [0, 2, 4, 5, 7, 9, 11], 1: [0, 2, 3, 5, 7, 8, 10]}


@dataclass
class Options:
    title: str = "未命名"
    key: str = "auto"                 # 'auto' 或 0..11
    meter: str = "auto"               # 'auto' / '2/4' / '3/4' / '4/4'
    bpm: str = "auto"                 # 'auto' 或数字
    melody_low: int = 55              # G3
    melody_high: int = 88             # E6
    with_bass: bool = True
    measures_per_system: int = 3
    systems_per_page: int = 4
    grid: int = 4                     # 每拍栅格数（4 = 十六分音符）
    max_seconds: Optional[float] = 300.0
    sensitivity: float = 0.5          # 0..1 越大越灵敏
    smooth: int = 5
    start_measure: int = 1
    engine: str = "viterbi"           # viterbi | onset
    tempo_prefer: str = "slow"        # slow | fast | none  速度倍率倾向


@dataclass
class Info:
    ok: bool = True
    message: str = ""
    bpm: float = 0.0
    key: str = ""
    key_confidence: float = 0.0
    meter: str = ""
    melody_notes: int = 0
    measures: int = 0
    duration: float = 0.0
    has_bass: bool = True
    elapsed: float = 0.0
    warnings: List[str] = field(default_factory=list)


def _estimate_tempo_pref(onset: np.ndarray, prefer: str = "slow"
                         ) -> Tuple[float, float, float]:
    """速度估计 + 倍率修正。

    自相关在 lag 与其 2 倍上往往同样强（速度八度歧义）。对旋律/伴奏型的
    流行曲，记谱上更常见的是「较慢」的读法（旋律为八分音符），因此当
    二倍 lag 的自相关仍足够强时，选择较慢的读法。
    """
    bpm, phase, conf = A.estimate_tempo(onset)
    if prefer == "none" or bpm <= 0:
        return bpm, phase, conf
    x = onset - onset.mean()
    n = x.size
    if n < 16:
        return bpm, phase, conf
    size = 1
    while size < 2 * n:
        size *= 2
    X = np.fft.rfft(x, size)
    ac = np.fft.irfft(X * np.conj(X), size)[:n].real
    ac[0] = 0.0
    if ac.max() <= 0:
        return bpm, phase, conf

    def ac_at(b: float) -> float:
        lag = int(round(60.0 / b * A.FPS))
        return float(ac[lag] / ac.max()) if 0 < lag < n else 0.0

    if prefer == "slow":
        cand = bpm / 2.0
        if cand >= 52 and ac_at(cand) >= 0.70:
            bpm = cand
    elif prefer == "fast":
        cand = bpm * 2.0
        if cand <= 200 and ac_at(cand) >= 0.70:
            bpm = cand
    # 相位重估
    period = 60.0 / bpm * A.FPS
    best_phase, best_val = 0.0, -1e9
    for ph in range(max(1, int(round(period)))):
        pos = np.arange(ph, n, period)
        idx = np.clip(np.round(pos).astype(int), 0, n - 1)
        v = float(onset[idx].sum())
        if v > best_val:
            best_val, best_phase = v, float(ph)
    return float(bpm), best_phase / A.FPS, conf


def parse_meter(meter: str) -> Tuple[int, int]:
    """'2/4' -> (2,4)。返回 (每小节拍数, 单位)。"""
    try:
        a, b = meter.split("/")
        return int(a), int(b)
    except Exception:
        return 2, 4


def meter_to_quarter_beats(beats: int, unit: int) -> int:
    return max(1, int(round(beats * 4.0 / unit)))


def estimate_meter(onset: np.ndarray, beat0: float, period_frames: float,
                   fps: float = A.FPS) -> str:
    """在 2/4、3/4、4/4 中挑一个使强拍更突出的。"""
    best, best_score = "4/4", -1e9
    for name, beats in (("2/4", 2), ("3/4", 3), ("4/4", 4)):
        for p in range(beats):
            pos = beat0 + (np.arange(0, 64) * beats + p) * period_frames
            idx = np.round(pos).astype(int)
            idx = idx[(idx >= 0) & (idx < onset.size)]
            if idx.size == 0:
                continue
            # 强拍均值 - 其他拍均值
            s = float(onset[idx].mean())
            allpos = beat0 + np.arange(0, 128) * period_frames
            ai = np.round(allpos).astype(int)
            ai = ai[(ai >= 0) & (ai < onset.size)]
            base = float(onset[ai].mean()) if ai.size else 0.0
            sc = s - base + 0.02 * beats
            if sc > best_score:
                best_score, best = sc, name
    return best


def _scale_semitone(degree: int, mode: str = "major") -> int:
    steps = _DIATONIC[0 if mode == "major" else 1]
    d = (degree - 1) % 7
    return steps[d]


def degree_to_midi(degree: int, octave: int, tonic_pc: int, mode: str = "major") -> int:
    ref = 60 + ((tonic_pc - 60) % 12)
    steps = _DIATONIC[0 if mode == "major" else 1]
    idx = (degree - 1) % 7
    carry = (degree - 1) // 7
    return ref + steps[idx] + 12 * (octave + carry)


# ------------------------------------------------------------------ 和弦
def detect_chords(sal: np.ndarray, key_tonic: int, mode: str,
                  frame_ranges: Sequence[Tuple[int, int]],
                  midi_low: int = A.MIDI_LOW) -> List[int]:
    """每小节和弦根音级数（1..7，大调音阶内）。"""
    steps = _DIATONIC[0 if mode == "major" else 1]
    pcs = (np.arange(sal.shape[0]) + midi_low) % 12
    # 低中音区权重（伴奏区）
    mids = np.arange(sal.shape[0]) + midi_low
    w = np.exp(-0.5 * ((mids - 55) / 16.0) ** 2).astype(np.float32)
    out = []
    for (t0, t1) in frame_ranges:
        t1 = max(t0 + 1, t1)
        block = sal[:, t0:t1]
        ch = np.zeros(12, dtype=np.float64)
        for pc in range(12):
            idx = np.where(pcs == pc)[0]
            if idx.size:
                ch[pc] = float((block[idx] * w[idx, None]).sum())
        if ch.sum() <= 0:
            out.append(1)
            continue
        chn = ch / ch.sum()
        best = (-1e9, 1)
        for di, st in enumerate(steps):
            root_pc = (key_tonic + st) % 12
            third = (root_pc + (4 if (st + 4) % 12 in steps or st in (0, 3, 4) else 3)) % 12
            # 大调音阶内的三和弦性质
            third_st = steps[(di + 2) % 7]
            fifth_st = steps[(di + 4) % 7]
            third = (key_tonic + third_st) % 12
            fifth = (key_tonic + fifth_st) % 12
            sc = chn[root_pc] * 1.0 + chn[third] * 0.75 + chn[fifth] * 0.75
            sc -= 0.25 * (chn.sum() - chn[root_pc] - chn[third] - chn[fifth])
            if sc > best[0]:
                best = (sc, di + 1)
        out.append(best[1])
    return out


# ------------------------------------------------------------------ 主流程
def analyze(path: str, opt: Optional[Options] = None,
            progress=None) -> Tuple[Score, Info]:
    t_start = time.time()
    opt = opt or Options()
    info = Info()

    def report(p: float, msg: str):
        if progress:
            try:
                progress(p, msg)
            except Exception:
                pass

    report(0.02, "解码音频…")
    x = A.decode_audio(path, max_seconds=opt.max_seconds)
    duration = x.size / A.SR
    info.duration = duration
    report(0.15, "频谱分析…")
    mag = A.stft_mag(x)
    M = A.build_salience_matrix()
    sal = A.salience(mag, M)
    report(0.35, "识别调性 / 速度…")

    # 调性
    if str(opt.key).lower() == "auto":
        tonic, mode, kconf = A.estimate_key(sal)
    else:
        try:
            tonic = int(opt.key) % 12
        except Exception:
            tonic = 0
        mode, kconf = "major", 1.0
    info.key = key_signature_ascii(tonic)
    info.key_confidence = round(float(kconf), 3)

    # 速度 / 拍点
    onset = A.onset_envelope(mag)
    if str(opt.bpm).lower() == "auto":
        bpm, phase_sec, tconf = _estimate_tempo_pref(onset, opt.tempo_prefer)
    else:
        try:
            bpm = float(opt.bpm)
        except Exception:
            bpm = 96.0
        _, phase_sec, tconf = A.estimate_tempo(onset)
        phase_sec = 0.0
    if bpm < 40 or bpm > 260:
        bpm = 96.0
    info.bpm = round(float(bpm), 2)

    period_frames = 60.0 / bpm * A.FPS
    beat0 = phase_sec * A.FPS
    # 强拍相位
    if str(opt.meter).lower() == "auto":
        meter = estimate_meter(onset, beat0, period_frames)
    else:
        meter = opt.meter
    mbeats, munit = parse_meter(meter)
    qbeats = meter_to_quarter_beats(mbeats, munit)
    info.meter = meter

    best_p, best_v = 0, -1e9
    for p in range(mbeats):
        pos = beat0 + (np.arange(0, 96) * mbeats + p) * period_frames
        idx = np.round(pos).astype(int)
        idx = idx[(idx >= 0) & (idx < onset.size)]
        if idx.size:
            v = float(onset[idx].mean())
            if v > best_v:
                best_v, best_p = v, p
    measure_beat0 = beat0 + best_p * period_frames

    report(0.5, "提取主旋律…")
    if opt.engine == "onset":
        raw_notes = A.notes_by_onset(sal, mel_low=opt.melody_low,
                                     mel_high=opt.melody_high,
                                     thr_ratio=0.16, min_gap=5, attack_frac=0.85)
        raw_notes = A.refine_octaves(raw_notes, sal)
    else:
        path_midi = A.viterbi_melody(sal, midi_low=A.MIDI_LOW, midi_high=A.MIDI_HIGH,
                                     prefer_low=opt.melody_low,
                                     prefer_high=opt.melody_high, onset=onset)
        path_midi = A.smooth_path(path_midi, opt.smooth)
        conf = A.melody_confidence(sal, path_midi)
        min_conf = 0.02 + 0.10 * (1.0 - max(0.0, min(1.0, opt.sensitivity)))
        raw_notes = A.segment_notes(path_midi, conf, min_conf=min_conf)
        raw_notes = A.refine_octaves(raw_notes, sal)
    # 限制在用户指定音域
    notes = [n for n in raw_notes if opt.melody_low <= n.midi <= opt.melody_high]
    if not notes:
        notes = raw_notes
    if not notes:
        info.ok = False
        info.message = "没有检测到旋律，请尝试提高灵敏度或更换音频。"
        return Score(title=opt.title, measures_per_system=opt.measures_per_system,
                     systems_per_page=opt.systems_per_page), info

    report(0.68, "量化节拍…")
    beat_dur = 60.0 / bpm
    first_beat_sec = measure_beat0 / A.FPS
    quant = A.quantize_notes(notes, bpm, first_beat_sec, qbeats, grid=opt.grid)
    if not quant:
        info.ok = False
        info.message = "量化后没有音符。"
        return Score(title=opt.title), info

    # 组织成小节
    by_measure: Dict[int, List[Tuple[float, int, float]]] = {}
    for mi, beat, midi, dur in quant:
        by_measure.setdefault(mi, []).append((beat, midi, dur))
    idxs = sorted(by_measure)
    # 去掉开头的空小节 / 太靠后的起始
    first_mi = idxs[0]
    if first_mi < 0:
        first_mi = 0
    measures: List[Measure] = []
    for k, mi in enumerate(idxs):
        if mi < first_mi:
            continue
        items = sorted(by_measure[mi], key=lambda t: t[0])
        mel: List[Event] = []
        for beat, midi, dur in items:
            if beat >= qbeats - 1e-6:
                continue
            dur = min(dur, qbeats - beat)
            if dur <= 0:
                continue
            if mel and abs(mel[-1].beat - beat) < 1e-6:
                # 同一位置重复（多为颤音/误检），保留较长的
                if dur > mel[-1].dur:
                    mel[-1] = Event(beat=beat, dur=dur, midi=midi)
                continue
            mel.append(Event(beat=beat, dur=dur, midi=midi))
        if not mel:
            continue
        measures.append(Measure(number=len(measures) + 1, melody=mel))
    if not measures:
        info.ok = False
        info.message = "没有可记谱的小节。"
        return Score(title=opt.title), info

    report(0.78, "分配指法…")
    # 右手指法
    for m in measures:
        mids = [e.midi for e in m.melody]
        fs = rh_fingering(mids)
        for e, f in zip(m.melody, fs):
            e.finger = f

    # 左手伴奏
    if opt.with_bass:
        report(0.85, "生成左手伴奏…")
        frame_ranges = []
        for m in measures:
            i0 = int(round(len(measures)))
        # 需要时间范围：用原始音符时间近似小节范围
        f0 = int(round(measure_beat0))
        per = period_frames
        for m in measures:
            t0 = f0 + int(round((m.number - 1) * qbeats * per))
            t1 = t0 + int(round(qbeats * per))
            frame_ranges.append((max(0, t0), min(sal.shape[1], max(t0 + 1, t1))))
        chords = detect_chords(sal, tonic, mode, frame_ranges)
        med = float(np.median([e.midi for m in measures for e in m.melody]))
        for m, root_deg in zip(measures, chords):
            m.chord_tones = [root_deg]
            m.chord = _chord_name(root_deg, tonic, mode)
            base = _pick_bass_octave(root_deg, med, tonic, mode)
            evs: List[Event] = []
            reps = max(1, qbeats // 2)
            for r in range(reps):
                for i in range(8):
                    d_raw = root_deg - 1 + FIG_DEGREE[i]
                    deg = d_raw % 7 + 1
                    carry = d_raw // 7
                    octv = base + FIG_OCTAVE[i] + carry
                    midi = degree_to_midi(deg, octv, tonic, mode)
                    if midi < PIANO_LOW:
                        midi += 12
                        octv += 1
                    if midi > PIANO_HIGH:
                        midi -= 12
                    evs.append(Event(beat=r * 2 + i * 0.25, dur=0.25, midi=midi,
                                     finger=FIG_FINGER[i]))
            evs = [e for e in evs if e.beat < qbeats - 1e-6]
            m.bass = evs

    score = Score(title=opt.title, tonic_pc=tonic, mode=mode, beats=qbeats, unit=4,
                  tempo=float(bpm), measures=measures,
                  measures_per_system=opt.measures_per_system,
                  systems_per_page=opt.systems_per_page,
                  meta={"source": path, "key_confidence": float(kconf)})
    info.melody_notes = sum(len(m.melody) for m in measures)
    info.measures = len(measures)
    info.has_bass = bool(opt.with_bass)
    info.elapsed = round(time.time() - t_start, 2)
    if len(measures) < 4:
        info.warnings.append("识别到的小节较少，可能是纯音乐/打击乐或灵敏度设置不合适。")
    report(1.0, "完成")
    return score, info


def _chord_name(degree: int, tonic: int, mode: str) -> str:
    steps = _DIATONIC[0 if mode == "major" else 1]
    root_pc = (tonic + steps[(degree - 1) % 7]) % 12
    third_st = steps[(degree - 1 + 2) % 7]
    third_pc = (tonic + third_st) % 12
    interval = (third_pc - root_pc) % 12
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return names[root_pc] + ("m" if interval == 3 else "")


def _pick_bass_octave(root_deg: int, melody_median: float,
                      tonic: int, mode: str) -> int:
    target = melody_median - 19.0
    best, bestd = -1, 1e9
    for o in range(-3, 3):
        m = degree_to_midi(root_deg, o, tonic, mode)
        if m < 28 or m > 62:
            continue
        d = abs(m - target)
        if d < bestd:
            bestd, best = d, o
    if best < 0:
        best = -1
    return best
