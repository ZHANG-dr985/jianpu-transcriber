"""简谱数据模型与乐理换算。

核心约定
--------
* ``Event.beat``  小节内位置，单位=拍（四分音符=1拍）
* ``Event.dur``   时值，单位=拍
* ``Event.midi``  MIDI 音高（21..108 为 88 键范围），None 表示休止
* 八度点：以含主音的那个八度（MIDI 60..71 区间内与主音同音名者）为基准 0
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple

# ---------------------------------------------------------------- 音名 / 调号
SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
# 1=X 的写法：优先用习惯记法
KEY_PREFER_FLAT = {1, 3, 5, 8, 10}          # Db Eb F Ab Bb
KEY_NAMES = {
    0: "C", 1: "bD", 2: "D", 3: "bE", 4: "E", 5: "F",
    6: "#F", 7: "G", 8: "bA", 9: "A", 10: "bB", 11: "B",
}
KEY_NAMES_FLAT_DISPLAY = {  # 渲染时用真正的音符符号
    1: "\u266dD", 3: "\u266dE", 6: "\u266fF", 8: "\u266dA", 10: "\u266dB",
}

MAJOR_STEPS = [0, 2, 4, 5, 7, 9, 11]
# 半音差 -> (级数, 变音记号)  记号: 1=升, -1=降, 0=本位
SEMITONE_TO_DEGREE = {
    0: (1, 0), 1: (1, 1), 2: (2, 0), 3: (3, -1), 4: (3, 0), 5: (4, 0),
    6: (4, 1), 7: (5, 0), 8: (6, -1), 9: (6, 0), 10: (7, -1), 11: (7, 0),
}
ACCIDENTAL_TEXT = {1: "\u266f", -1: "\u266d", 0: ""}

# 88 键钢琴范围
PIANO_LOW, PIANO_HIGH = 21, 108
PIANO_KEYS = PIANO_HIGH - PIANO_LOW + 1

# 音区与唱名（用于键盘提示）
SOLFEGE = {1: "do", 2: "re", 3: "mi", 4: "fa", 5: "sol", 6: "la", 7: "si"}


def midi_to_name(midi: int) -> str:
    if midi is None:
        return "-"
    octave = midi // 12 - 1
    return f"{SHARP_NAMES[midi % 12]}{octave}"


def key_signature_label(tonic_pc: int) -> str:
    """返回 1=X 里的 X（用 ♯/♭ 符号）。"""
    return KEY_NAMES_FLAT_DISPLAY.get(tonic_pc, SHARP_NAMES[tonic_pc])


def key_signature_ascii(tonic_pc: int) -> str:
    return KEY_NAMES.get(tonic_pc, SHARP_NAMES[tonic_pc])


def scale_degree(midi: int, tonic_pc: int) -> Tuple[int, int, int]:
    """(级数, 变音记号, 八度点)。八度点 >0 记在数字上方，<0 记在下方。"""
    semis = (midi - tonic_pc) % 12
    degree, acc = SEMITONE_TO_DEGREE[semis]
    ref_base = 60 + ((tonic_pc - 60) % 12)      # 含主音的基准八度起点
    octave = (midi - ref_base) // 12
    return degree, acc, octave


def midi_to_jianpu(midi: int, tonic_pc: int) -> str:
    """文本形式，例如 '3'、'#4'、'i'（高八度用后缀 ' 表示）。"""
    if midi is None:
        return "0"
    degree, acc, octave = scale_degree(midi, tonic_pc)
    text = ACCIDENTAL_TEXT[acc] + str(degree)
    if octave > 0:
        text += "'" * octave
    elif octave < 0:
        text += "," * (-octave)
    return text


# ---------------------------------------------------------------- 数据模型
@dataclass
class Event:
    """一个简谱音符。"""
    beat: float                     # 小节内起拍位置
    dur: float                      # 时值（拍）
    midi: Optional[int] = None      # MIDI 音高；None=休止
    finger: Optional[int] = None    # 指法 1..5
    degree: int = 0
    accidental: int = 0
    octave: int = 0
    slur: bool = False              # 与下一个音连音/圆滑线
    tie: bool = False               # 跨小节延音线
    source: str = ""                # melody / bass / user

    def __post_init__(self):
        if self.midi is not None and self.degree == 0:
            self.recalc()

    def recalc(self, tonic_pc: int = 0):
        if self.midi is None:
            self.degree, self.accidental, self.octave = 0, 0, 0
        else:
            self.degree, self.accidental, self.octave = scale_degree(self.midi, tonic_pc)

    @property
    def is_rest(self) -> bool:
        return self.midi is None

    def jianpu_text(self, tonic_pc: int = 0) -> str:
        if self.midi is None:
            return "0"
        d, a, o = scale_degree(self.midi, tonic_pc)
        t = ACCIDENTAL_TEXT[a] + str(d)
        t += "'" * o if o > 0 else "," * (-o)
        return t


@dataclass
class Measure:
    number: int = 1
    melody: List[Event] = field(default_factory=list)
    bass: List[Event] = field(default_factory=list)
    chord: str = ""                 # 例如 "C" / "Am"
    chord_tones: List[int] = field(default_factory=list)   # 和弦音级数 1/3/5

    def beats_used(self) -> float:
        return max((e.beat + e.dur for e in self.melody), default=0.0)


@dataclass
class Score:
    title: str = "未命名"
    tonic_pc: int = 0               # 0=C
    mode: str = "major"
    beats: int = 2                  # 每小节拍数
    unit: int = 4                   # 以几分音符为一拍
    tempo: float = 96.0
    measures: List[Measure] = field(default_factory=list)
    measures_per_system: int = 3
    systems_per_page: int = 4
    melody_track: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def key_label(self) -> str:
        return key_signature_label(self.tonic_pc)

    @property
    def meter_label(self) -> str:
        return f"{self.beats}/{self.unit}"

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Score":
        s = Score(title=d.get("title", "未命名"), tonic_pc=d.get("tonic_pc", 0),
                  mode=d.get("mode", "major"), beats=d.get("beats", 2),
                  unit=d.get("unit", 4), tempo=d.get("tempo", 96.0),
                  measures_per_system=d.get("measures_per_system", 3),
                  systems_per_page=d.get("systems_per_page", 4),
                  melody_track=d.get("melody_track", ""), meta=d.get("meta", {}))
        for m in d.get("measures", []):
            mea = Measure(number=m.get("number", 1), chord=m.get("chord", ""),
                          chord_tones=m.get("chord_tones", []))
            for e in m.get("melody", []):
                ev = Event(**{k: e.get(k) for k in
                              ("beat", "dur", "midi", "finger", "slur", "tie") if k in e})
                ev.source = "melody"
                ev.recalc(s.tonic_pc)
                mea.melody.append(ev)
            for e in m.get("bass", []):
                ev = Event(**{k: e.get(k) for k in
                              ("beat", "dur", "midi", "finger", "slur", "tie") if k in e})
                ev.source = "bass"
                ev.recalc(s.tonic_pc)
                mea.bass.append(ev)
            s.measures.append(mea)
        return s


# ---------------------------------------------------------------- 时值 -> 符号
@dataclass
class Rhythm:
    underlines: int = 0     # 减时线数量
    dots: int = 0           # 附点数量
    dashes: int = 0         # 增时线（"-"）数量


def rhythm_of(dur: float) -> Rhythm:
    """把时值（拍，四分音符=1）转换成简谱记号。"""
    d = round(dur * 8) / 8.0            # 量化到 1/32 拍
    if d <= 0:
        return Rhythm()
    # 小于一拍：用减时线 + 附点
    if d < 1:
        table = {
            0.125: Rhythm(3, 0, 0), 0.1875: Rhythm(3, 1, 0),
            0.25: Rhythm(2, 0, 0), 0.375: Rhythm(2, 1, 0),
            0.5: Rhythm(1, 0, 0), 0.75: Rhythm(1, 1, 0),
        }
        if d in table:
            return table[d]
        # 近似
        if d <= 0.1875:
            return Rhythm(3, 0, 0)
        if d <= 0.375:
            return Rhythm(2, 1, 0) if d > 0.25 else Rhythm(2, 0, 0)
        return Rhythm(1, 1, 0) if d > 0.5 else Rhythm(1, 0, 0)
    # 大于等于一拍：整拍用增时线，其余用附点
    base = 1
    while base * 2 <= d:
        base *= 2
    if abs(d - base * 1.5) < 1e-6:
        return Rhythm(0, 1, base - 1)
    if abs(d - base) < 1e-6:
        return Rhythm(0, 0, base - 1)
    dashes = int(d) - 1
    if dashes < 0:
        dashes = 0
    return Rhythm(0, 0, dashes)
