"""导出：可编辑文本 / 逐音对照表 / MIDI。"""
from __future__ import annotations

import struct
from typing import List, Optional, Tuple

from .fingering import key_position
from .model import (ACCIDENTAL_TEXT, Event, Measure, Score, key_signature_ascii,
                    midi_to_name, rhythm_of, scale_degree)


# ------------------------------------------------------------------ 文本
def _fmt_octave(octave: int) -> str:
    return "+" * octave if octave > 0 else "-" * (-octave)


def dump_text(score: Score) -> str:
    """可再次解析的乐谱文本。

    音符记号: 起拍:级数[#/b][+/-八度][@指法]:时值(拍)
    """
    lines = [
        "# 简谱扒谱结果（可手工修改后用「文本重新排版」再渲染）",
        "# 音符记号  起拍:音:时值    例如  0:5@1:0.5  = 第0拍上、简谱5、一指、半拍",
        "# 八度         3+  表示高八度3     3-  表示低八度3",
        "# 变音         #4 / b7            指法  @1..@5",
        "# 休止         0:0:1",
        f"title={score.title}",
        f"key={key_signature_ascii(score.tonic_pc)}",
        f"meter={score.beats}/{score.unit}",
        f"tempo={round(score.tempo, 2)}",
        f"measures_per_system={score.measures_per_system}",
        f"systems_per_page={score.systems_per_page}",
        "",
    ]
    for m in score.measures:
        lines.append(f"[{m.number}] chord={m.chord}")
        lines.append("M " + " ".join(_event_token(e) for e in m.melody))
        if m.bass:
            lines.append("B " + " ".join(_event_token(e) for e in m.bass))
    return "\n".join(lines) + "\n"


def _event_token(e: Event) -> str:
    if e.midi is None:
        pitch = "0"
    else:
        tonic = 0
        # 与调无关的相对写法：以 C 为 1（内部再按 1=X 解释）
        d, a, o = scale_degree(e.midi, tonic)
        acc = {1: "#", -1: "b", 0: ""}[a]
        pitch = f"{d}{acc}{_fmt_octave(o)}"
        if e.finger:
            pitch += f"@{e.finger}"
    return f"{round(e.beat, 4)}:{pitch}:{round(e.dur, 4)}"


def load_text(text: str, tonic_default: int = 0) -> Score:
    score = Score()
    measures: List[Measure] = []
    cur: Optional[Measure] = None
    names = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
             "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10,
             "Bb": 10, "B": 11}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            num = int(line[1:line.index("]")])
            chord = ""
            if "chord=" in line:
                chord = line.split("chord=", 1)[1].strip()
            cur = Measure(number=num, chord=chord)
            measures.append(cur)
            continue
        if "=" in line and not line[0] in "MB" and ":" not in line.split("=")[0]:
            k, v = line.split("=", 1)
            k, v = k.strip().lower(), v.strip()
            if k == "title":
                score.title = v
            elif k == "key":
                pc = names.get(v.upper().replace("b", "b"), None)
                if pc is None:
                    for kk, vv in names.items():
                        if kk.upper() == v.upper():
                            pc = vv
                score.tonic_pc = pc if pc is not None else tonic_default
            elif k == "meter":
                a, b = v.split("/")
                score.beats, score.unit = int(a), int(b)
            elif k == "tempo":
                score.tempo = float(v)
            elif k == "measures_per_system":
                score.measures_per_system = int(v)
            elif k == "systems_per_page":
                score.systems_per_page = int(v)
            continue
        if line[0] in "MB" and len(line) > 1:
            if cur is None:
                cur = Measure(number=1)
                measures.append(cur)
            target = cur.melody if line[0] == "M" else cur.bass
            for tok in line[1:].split():
                ev = _parse_token(tok)
                if ev is not None:
                    target.append(ev)
    if not measures:
        raise ValueError("文本里没有小节数据")
    for m in measures:
        for e in m.melody + m.bass:
            e.recalc(score.tonic_pc)
    score.measures = measures
    return score


def _parse_token(tok: str) -> Optional[Event]:
    """解析  起拍:音:时值   或旧式  音:时值。"""
    if not tok:
        return None
    parts = tok.split(":")
    beat = 0.0
    if len(parts) >= 3:
        try:
            beat = float(parts[0])
        except ValueError:
            beat = 0.0
        head, dur_s = parts[1], parts[2]
    elif len(parts) == 2:
        head, dur_s = parts[0], parts[1]
    else:
        head, dur_s = parts[0], "1"
    try:
        dur = float(dur_s)
    except ValueError:
        dur = 1.0
    finger = None
    if "@" in head:
        head, fs = head.split("@", 1)
        try:
            finger = int(fs)
        except ValueError:
            finger = None
    if head in ("0", "-", ""):
        return Event(beat=beat, dur=dur, midi=None, finger=finger)
    acc = 0
    while head and head[0] in "#b":
        acc += 1 if head[0] == "#" else -1
        head = head[1:]
    octave = 0
    while head and head[-1] in "+-":
        octave += 1 if head[-1] == "+" else -1
        head = head[:-1]
    try:
        degree = int(head)
    except ValueError:
        return None
    midi = 60 + _degree_semitone(degree) + 12 * octave + acc
    return Event(beat=beat, dur=dur, midi=midi, finger=finger)


def _degree_semitone(degree: int) -> int:
    steps = [0, 2, 4, 5, 7, 9, 11]
    return steps[(degree - 1) % 7] + 12 * ((degree - 1) // 7)


# 位置补全：文本里没写起拍时按顺序重排
def fix_beats(score: Score) -> Score:
    for m in score.measures:
        for group in (m.melody, m.bass):
            if any(e.beat for e in group):
                continue
            t = 0.0
            for e in group:
                e.beat = t
                t += e.dur
    return score


def human_text(score: Score) -> str:
    """逐小节对照表：简谱 / 音名 / 指法 / 键盘位置。"""
    out = [f"《{score.title}》  1={score.key_label}  {score.meter_label}  "
           f"♩={round(score.tempo)}"]
    for m in score.measures:
        out.append("")
        out.append(f"── 第 {m.number} 小节   和弦 {m.chord or '-'}")
        if m.melody:
            out.append("  右手旋律：")
            for e in m.melody:
                out.append("    " + _note_line(e, score, "r"))
        if m.bass:
            out.append("  左手伴奏：")
            for e in m.bass:
                out.append("    " + _note_line(e, score, "l"))
    return "\n".join(out) + "\n"


def _note_line(e: Event, score: Score, hand: str) -> str:
    d, a, o = scale_degree(e.midi, score.tonic_pc) if e.midi is not None else (0, 0, 0)
    dot = (("上" * o) if o > 0 else ("下" * (-o))) + "加八度点" if o else "无八度点"
    fing = f"{e.finger} 指" if e.finger else "-"
    hand_txt = "右手" if hand == "r" else "左手"
    name = midi_to_name(e.midi) if e.midi is not None else "休止"
    return (f"第 {e.beat + 1:>4.2f} 拍  {name:>4}  →  简谱 {e.jianpu_text(score.tonic_pc):<5}"
            f"{dot:<10} 时值 {e.dur:g} 拍   {hand_txt}指法 {fing:<4}"
            f"88 键: {key_position(e.midi)}")


# ------------------------------------------------------------------ MIDI
def _vlq(n: int) -> bytes:
    out = bytearray([n & 0x7F])
    n >>= 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


def _track(events: List[Tuple[int, bytes]]) -> bytes:
    events = sorted(events, key=lambda t: t[0])
    body = bytearray()
    last = 0
    for tick, data in events:
        body += _vlq(max(0, tick - last))
        body += data
        last = tick
    body += _vlq(0) + b"\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def export_midi(score: Score, path: str, tp_quarter: int = 480) -> str:
    beat_dur = 60.0 / max(1e-6, score.tempo)
    # 拍 -> tick（拍=四分音符）
    def tick(beat: float) -> int:
        return int(round(beat * tp_quarter))

    mel_ev: List[Tuple[int, bytes]] = []
    bas_ev: List[Tuple[int, bytes]] = []
    offset = 0
    meas_beats = score.beats
    for m in score.measures:
        for e, evs in ((m.melody, mel_ev), (m.bass, bas_ev)):
            for x in e:
                if x.midi is None:
                    continue
                t0 = offset + tick(x.beat)
                t1 = t0 + max(1, tick(x.dur))
                ch = 0 if evs is mel_ev else 1
                evs.append((t0, bytes([0x90 | ch, x.midi, 90])))
                if x.finger:
                    tag = f"{x.finger}".encode()
                    evs.append((t0, b"\xff\x06" + _vlq(len(tag)) + tag))
                evs.append((t1, bytes([0x80 | ch, x.midi, 0])))
        offset += tick(meas_beats)

    tempo_track = [(0, b"\xff\x51\x03" + struct.pack(">I", int(60_000_000 / max(1.0, score.tempo)))[1:]),
                   (0, b"\xff\x03" + _vlq(len(score.title.encode("utf-8"))) + score.title.encode("utf-8"))]
    data = b"MThd" + struct.pack(">IHHH", 6, 1, 3, tp_quarter)
    data += _track(tempo_track) + _track(mel_ev) + _track(bas_ev)
    with open(path, "wb") as f:
        f.write(data)
    return path
