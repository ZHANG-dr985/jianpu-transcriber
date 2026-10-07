"""音频分析：解码 / 音高显著性 / 起始点 / 速度 / 调性 / 主旋律提取。

仅依赖 numpy + scipy + ffmpeg，不依赖 librosa。
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

SR = 22050
N_FFT = 2048
HOP = 256
FPS = SR / HOP                       # ≈86.13 帧/秒
MIDI_LOW, MIDI_HIGH = 21, 108        # 88 键

_FFMPEG_CANDIDATES = [
    "ffmpeg",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    os.path.join(os.path.dirname(sys.executable), "ffmpeg.exe"),
]


def find_ffmpeg() -> Optional[str]:
    for c in _FFMPEG_CANDIDATES:
        p = shutil.which(c) if os.path.basename(c) == c else (c if os.path.exists(c) else None)
        if p:
            return p
    return None


# ------------------------------------------------------------------ 解码
def decode_audio(path: str, sr: int = SR, max_seconds: Optional[float] = None) -> np.ndarray:
    """用 ffmpeg 解码为单声道 float32。"""
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("未找到 ffmpeg，无法解码音频。请安装 ffmpeg 或使用 WAV 文件。")
    cmd = [ff, "-v", "error", "-nostdin", "-i", path]
    if max_seconds:
        cmd += ["-t", f"{max_seconds:.3f}"]
    cmd += ["-f", "f32le", "-acodec", "pcm_f32le", "-ac", "1", "-ar", str(sr), "-"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg 无法启动")
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "ignore").strip()[-500:]
        raise RuntimeError(f"ffmpeg 解码失败: {msg}")
    data = np.frombuffer(proc.stdout, dtype=np.float32)
    if data.size == 0:
        raise RuntimeError("解码结果为空，请检查音频文件")
    # 归一化
    peak = float(np.max(np.abs(data)))
    if peak > 0:
        data = data / max(1.0, peak)
    return np.ascontiguousarray(data, dtype=np.float32)


# ------------------------------------------------------------------ STFT
def stft_mag(x: np.ndarray, n_fft: int = N_FFT, hop: int = HOP) -> np.ndarray:
    if x.size < n_fft:
        x = np.pad(x, (0, n_fft - x.size))
    win = np.hanning(n_fft).astype(np.float32)
    n_frames = 1 + (x.size - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = x[idx] * win[None, :]
    spec = np.fft.rfft(frames, axis=1)
    return np.abs(spec).astype(np.float32)          # (T, F)


# ------------------------------------------------------------------ 显著性
def build_salience_matrix(sr: int = SR, n_fft: int = N_FFT,
                          midi_low: int = MIDI_LOW, midi_high: int = MIDI_HIGH,
                          harmonics: int = 6) -> np.ndarray:
    """返回 (n_notes, n_bins) 的谐波求和矩阵。"""
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    n_bins = freqs.size
    n_notes = midi_high - midi_low + 1
    M = np.zeros((n_notes, n_bins), dtype=np.float32)
    df = freqs[1] - freqs[0]
    for i, midi in enumerate(range(midi_low, midi_high + 1)):
        f0 = 440.0 * 2 ** ((midi - 69) / 12.0)
        for h in range(1, harmonics + 1):
            f = f0 * h
            if f >= freqs[-1] or f < freqs[1]:
                continue
            pos = f / df
            lo = int(pos)
            frac = pos - lo
            w = (0.84 ** (h - 1)) / math.sqrt(h)
            if lo + 1 < n_bins:
                M[i, lo] += w * (1 - frac)
                M[i, lo + 1] += w * frac
            else:
                M[i, lo] += w
    return M


def salience(mag: np.ndarray, M: np.ndarray, chunk: int = 512) -> np.ndarray:
    """mag: (T, F) -> (n_notes, T)"""
    T = mag.shape[0]
    out = np.empty((M.shape[0], T), dtype=np.float32)
    for s in range(0, T, chunk):
        e = min(T, s + chunk)
        out[:, s:e] = M @ mag[s:e].T
    return out


# ------------------------------------------------------------------ 起始点
def onset_envelope(mag: np.ndarray) -> np.ndarray:
    logm = np.log1p(mag * 20.0)
    diff = np.diff(logm, axis=0)
    diff[diff < 0] = 0
    flux = diff.sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    # 去均值 + 平滑
    flux = flux - flux.mean()
    k = np.array([0.25, 0.5, 1.0, 0.5, 0.25], dtype=np.float32)
    k /= k.sum()
    return np.convolve(flux, k, mode="same").astype(np.float32)


# ------------------------------------------------------------------ 速度
def estimate_tempo(onset: np.ndarray, fps: float = FPS,
                   bpm_min: float = 50.0, bpm_max: float = 200.0
                   ) -> Tuple[float, float, float]:
    """返回 (bpm, 首拍时间(秒), 置信度)。"""
    x = onset - onset.mean()
    n = x.size
    if n < 8:
        return 96.0, 0.0, 0.0
    size = 1
    while size < 2 * n:
        size *= 2
    X = np.fft.rfft(x, size)
    ac = np.fft.irfft(X * np.conj(X), size)[:n].real
    ac[0] = 0.0
    lag_min = max(1, int(round(fps * 60.0 / bpm_max)))
    lag_max = min(n - 1, int(round(fps * 60.0 / bpm_min)))
    if lag_max <= lag_min:
        return 96.0, 0.0, 0.0
    lags = np.arange(lag_min, lag_max + 1)
    vals = ac[lags].copy()
    # 对数正态先验（偏向 115 BPM）
    bpms = 60.0 * fps / lags
    prior = np.exp(-0.5 * ((np.log2(bpms / 115.0)) / 0.9) ** 2)
    score = vals * prior
    best = int(np.argmax(score))
    lag = float(lags[best])
    # 抛物线细化
    if 0 < best < len(lags) - 1:
        y0, y1, y2 = score[best - 1], score[best], score[best + 1]
        denom = (y0 - 2 * y1 + y2)
        if abs(denom) > 1e-12:
            off = 0.5 * (y0 - y2) / denom
            lag += off
    bpm = 60.0 * fps / lag
    conf = float(score[best] / (np.median(score) + 1e-9))
    # 相位
    period = lag
    best_phase, best_val = 0.0, -1e9
    for ph in range(max(1, int(round(period)))):
        pos = np.arange(ph, n, period)
        idx = np.clip(np.round(pos).astype(int), 0, n - 1)
        v = float(onset[idx].sum())
        if v > best_val:
            best_val, best_phase = v, float(ph)
    return float(bpm), best_phase / fps, conf


# ------------------------------------------------------------------ 调性
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def chroma_from_salience(sal: np.ndarray, midi_low: int = MIDI_LOW) -> np.ndarray:
    n_notes = sal.shape[0]
    pcs = (np.arange(n_notes) + midi_low) % 12
    ch = np.zeros(12, dtype=np.float64)
    for pc in range(12):
        idx = np.where(pcs == pc)[0]
        ch[pc] = sal[idx].sum()
    return ch


def estimate_key(sal: np.ndarray, midi_low: int = MIDI_LOW) -> Tuple[int, str, float]:
    ch = chroma_from_salience(sal, midi_low)
    if ch.sum() <= 0:
        return 0, "major", 0.0
    ch = ch / ch.sum()
    best = (-2.0, 0, "major")
    for tonic in range(12):
        for mode, prof in (("major", _MAJOR), ("minor", _MINOR)):
            p = np.roll(prof, tonic)
            p = p / p.sum()
            r = float(np.corrcoef(ch, p)[0, 1])
            if r > best[0]:
                best = (r, tonic, mode)
    return best[1], best[2], best[0]


# ------------------------------------------------------------------ 主旋律 Viterbi
def viterbi_melody(sal: np.ndarray, fps: float = FPS,
                   midi_low: int = MIDI_LOW, midi_high: int = MIDI_HIGH,
                   prefer_low: int = 55, prefer_high: int = 88,
                   emit: float = 2.0, jump: float = 0.18, switch: float = 0.45,
                   octave_penalty: float = 1.0, slope: float = 0.0,
                   onset: Optional[np.ndarray] = None,
                   gate: float = 0.0, gate_cost: float = 1.2) -> np.ndarray:
    """逐帧主旋律音高（Viterbi）。

    关键点：每帧发射概率先做「帧内归一化」再压缩到 [-emit, 0]，
    这样跳进代价才有意义，否则频谱最大值会完全支配路径。
    """
    S = np.asarray(sal, dtype=np.float32)
    n_notes, T = S.shape
    L = np.log1p(S * 40.0)
    mx = L.max(axis=0, keepdims=True)
    rng = np.maximum(mx - L.min(axis=0, keepdims=True), 1.0)
    L = (L - mx) / rng * emit                    # [-emit, 0]
    midis = np.arange(midi_low, midi_high + 1)
    if slope:
        L = L + (slope * (midis - 60)).astype(np.float32)[:, None]

    d = np.abs(midis[:, None] - midis[None, :]).astype(np.float32)
    base = switch + jump * np.minimum(d, 12.0) + octave_penalty * (d >= 12)
    np.fill_diagonal(base, 0.0)

    strong = None
    if onset is not None and gate > 0:
        strong = (onset > gate).astype(bool)

    dp = L[:, 0].copy()
    bp = np.zeros((T, n_notes), dtype=np.int16)
    for t in range(1, T):
        c = base
        if strong is not None and not strong[t]:
            c = base + gate_cost            # 非起音帧：禁止换音
        m = dp[None, :] - c
        arg = np.argmax(m, axis=1)
        dp = m[np.arange(n_notes), arg] + L[:, t]
        bp[t] = arg.astype(np.int16)
    path = np.empty(T, dtype=np.int16)
    cur = int(np.argmax(dp))
    for t in range(T - 1, -1, -1):
        path[t] = midis[cur]
        if t:
            cur = int(bp[t, cur])
    return path


def melody_voicing(sal: np.ndarray, fps: float = FPS,
                   midi_low: int = MIDI_LOW,
                   mel_low: int = 55, mel_high: int = 88,
                   rel: float = 0.18) -> np.ndarray:
    """判断每帧是否有「主旋律」在响：主旋律音区能量占比较高的帧。"""
    mids = np.arange(sal.shape[0]) + midi_low
    hi = (mids >= mel_low) & (mids <= mel_high)
    lo = ~hi
    e_hi = sal[hi].max(axis=0) if hi.any() else np.zeros(sal.shape[1], dtype=np.float32)
    e_lo = sal[lo].max(axis=0) if lo.any() else np.zeros(sal.shape[1], dtype=np.float32)
    ref = np.percentile(e_hi, 90) + 1e-9
    active = (e_hi > rel * ref * 0.35)
    dom = e_hi >= 0.72 * np.maximum(e_lo, 1e-9)
    v = (active & dom).astype(np.float32)
    # 轻度平滑：短空档不算断开
    k = np.ones(5, dtype=np.float32) / 5
    vs = np.convolve(v, k, mode="same")
    return (vs > 0.34).astype(np.float32)


def melody_confidence(sal: np.ndarray, path: np.ndarray, midi_low: int = MIDI_LOW) -> np.ndarray:
    """每帧旋律置信度（相对能量）。"""
    idx = np.clip(path - midi_low, 0, sal.shape[0] - 1)
    val = sal[idx, np.arange(sal.shape[1])]
    tot = sal.sum(axis=0) + 1e-9
    return (val / tot).astype(np.float32)


# ------------------------------------------------------------------ 音符切分
@dataclass
class NoteEvent:
    start: float
    end: float
    midi: int
    conf: float = 0.0
    energy: float = 0.0

    @property
    def dur(self) -> float:
        return max(0.0, self.end - self.start)


def smooth_path(path: np.ndarray, win: int = 5) -> np.ndarray:
    if win <= 1 or path.size < win:
        return path.astype(np.int16)
    out = path.astype(np.int16).copy()
    half = win // 2
    for i in range(path.size):
        a, b = max(0, i - half), min(path.size, i + half + 1)
        seg = path[a:b]
        vals, counts = np.unique(seg, return_counts=True)
        out[i] = vals[np.argmax(counts)]
    return out


def segment_notes(path: np.ndarray, conf: np.ndarray, fps: float = FPS,
                  min_conf: float = 0.06, min_dur: float = 0.075,
                  gap_merge: float = 0.06) -> List[NoteEvent]:
    """把逐帧音高轨迹切成音符事件。"""
    T = path.size
    if T == 0:
        return []
    notes: List[NoteEvent] = []
    i = 0
    while i < T:
        if conf[i] < min_conf or path[i] <= 0:
            i += 1
            continue
        j = i + 1
        while j < T and conf[j] >= min_conf and abs(int(path[j]) - int(path[i])) < 1:
            j += 1
        seg_conf = conf[i:j]
        notes.append(NoteEvent(i / fps, j / fps, int(path[i]),
                               float(seg_conf.mean()), float(seg_conf.sum())))
        i = j
    # 合并同音高的小间隙 / 抖动
    merged: List[NoteEvent] = []
    for n in notes:
        if merged:
            p = merged[-1]
            if abs(p.midi - n.midi) <= 0 and (n.start - p.end) <= gap_merge:
                p.end = n.end
                p.conf = max(p.conf, n.conf)
                continue
            if abs(p.midi - n.midi) == 1 and (n.start - p.end) <= 0 and n.dur < min_dur:
                continue
        merged.append(n)
    # 去掉过短音符（保留能量较大的）
    out = [n for n in merged if n.dur >= min_dur]
    if not out and merged:
        out = [max(merged, key=lambda x: x.dur)]
    return out


# ------------------------------------------------------------------ 量化
# ------------------------------------------------------------------ 起音分段法
def melodic_onset_env(sal: np.ndarray, mel_low: int = 55, mel_high: int = 88,
                      midi_low: int = MIDI_LOW) -> np.ndarray:
    mids = np.arange(sal.shape[0]) + midi_low
    band = (mids >= mel_low) & (mids <= mel_high)
    e = sal[band].sum(axis=0)
    e = np.log1p(e * 20.0)
    d = np.diff(e)
    d[d < 0] = 0
    d = np.concatenate([[0.0], d])
    k = np.ones(3, dtype=np.float32) / 3
    return np.convolve(d, k, mode="same")


def pick_peaks(env: np.ndarray, min_gap: int = 6, thr_ratio: float = 0.2) -> np.ndarray:
    if env.size == 0:
        return np.array([], dtype=int)
    thr = max(float(env.max()) * thr_ratio, 0.0)
    peaks: List[int] = []
    last = -10 ** 9
    for i in range(1, env.size - 1):
        if env[i] >= env[i - 1] and env[i] > env[i + 1] and env[i] > thr:
            if i - last >= min_gap:
                peaks.append(i)
                last = i
            elif peaks and env[i] > env[peaks[-1]]:
                peaks[-1] = i
                last = i
    return np.array(peaks, dtype=int)


def notes_by_onset(sal: np.ndarray, mel_low: int = 55, mel_high: int = 88,
                   thr_ratio: float = 0.2, min_gap: int = 6,
                   attack_frac: float = 0.8, min_len: int = 6,
                   voicing_rel: float = 0.10, skyline: float = 0.0,
                   fps: float = FPS, midi_low: int = MIDI_LOW) -> List[NoteEvent]:
    """按起音把旋律切成段，取段内主旋律音区的主导音高。

    对「延音中伴奏抢戏」的情况比连续 Viterbi 稳健。
    """
    env = melodic_onset_env(sal, mel_low, mel_high, midi_low)
    peaks = list(pick_peaks(env, min_gap=min_gap, thr_ratio=thr_ratio))
    T = sal.shape[1]
    if not peaks:
        return []
    bounds = peaks + [T]
    mids = np.arange(sal.shape[0]) + midi_low
    band = (mids >= mel_low) & (mids <= mel_high)
    if not band.any():
        return []
    ref = float(np.percentile(sal[band].max(axis=0), 90)) + 1e-9
    out: List[NoteEvent] = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        if b - a < min_len:
            continue
        n_att = max(3, int((b - a) * attack_frac))
        prof = sal[:, a:a + n_att].mean(axis=1)
        prof = prof.copy()
        prof[~band] = -1.0
        if skyline > 0:
            cand = np.where(prof >= skyline * prof.max())[0]
            m = int(cand.max()) if cand.size else int(np.argmax(prof))
        else:
            m = int(np.argmax(prof))
        if prof[m] < voicing_rel * ref:
            continue
        out.append(NoteEvent(a / fps, b / fps, int(mids[m]),
                             float(min(1.0, prof[m] / ref)), float(prof[m])))
    return out


def refine_octaves(notes: Sequence[NoteEvent], sal: np.ndarray,
                   midi_low: int = MIDI_LOW, midi_high: int = MIDI_HIGH,
                   up_ratio: float = 1.35) -> List[NoteEvent]:
    """八度纠正：若高八度的显著度明显更强，说明原音高是第一泛音误判。"""
    out: List[NoteEvent] = []
    for n in notes:
        t0 = max(0, int(n.start * FPS))
        t1 = max(t0 + 1, int(n.end * FPS))
        m = n.midi
        i = m - midi_low
        if not (0 <= i < sal.shape[0]) or t1 <= t0:
            out.append(n)
            continue
        cur = float(sal[i, t0:t1].mean())
        up = m + 12 - midi_low
        if 0 <= up < sal.shape[0]:
            v_up = float(sal[up, t0:t1].mean())
            if v_up > cur * up_ratio:
                n = NoteEvent(n.start, n.end, m + 12, n.conf, n.energy)
        out.append(n)
    return out


def quantize_notes(notes: Sequence[NoteEvent], bpm: float, first_beat: float,
                   beats_per_measure: int, grid: int = 4,
                   start_offset_beats: float = 0.0) -> List[Tuple[int, float, int, float]]:
    """量化为 (小节序号, 拍位置, midi, 时值拍)。grid=4 表示十六分音符栅格。"""
    if bpm <= 0:
        bpm = 96.0
    beat_dur = 60.0 / bpm
    step = beat_dur / grid
    total_beats = beats_per_measure

    def snap(t: float) -> float:
        k = round((t - first_beat) / step)
        return first_beat + k * step

    out = []
    for n in notes:
        if n.midi <= 0:
            continue
        s = snap(n.start)
        e = snap(n.end)
        if e <= s:
            e = s + step
        # 转成拍
        sb = (s - first_beat) / beat_dur + start_offset_beats
        eb = (e - first_beat) / beat_dur + start_offset_beats
        mi = int(math.floor(sb / total_beats + 1e-6))
        beat = sb - mi * total_beats
        dur = max(1.0 / grid, eb - sb)
        out.append((mi, round(beat, 4), int(n.midi), round(dur, 4)))
    return out
