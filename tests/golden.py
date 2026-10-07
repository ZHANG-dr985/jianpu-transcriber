"""黄金样板：《花又落》第 1 页（按参考图逐音手动录入）。

用途：验证渲染器的版式是否与用户提供的图片一致。
运行： python tests/golden.py
输出： out/golden_p1.png  以及 out/compare.png（与参考图并排对比）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from jianpu.model import Event, Measure, Score
from jianpu.render import Renderer

# 简谱级数 -> 半音（相对主音 C），配合八度
STEPS = {1: 0, 2: 2, 3: 4, 4: 5, 5: 7, 6: 9, 7: 11}


def midi_of(degree: int, octave: int, tonic_pc: int = 0) -> int:
    """degree: 1..7, octave: 八度点数（0=基准八度）。"""
    base = 60 + ((tonic_pc - 60) % 12)
    return base + STEPS[degree] + 12 * octave


def mel(beat, dur, degree, octave, finger=None, slur=False, tie=False) -> Event:
    return Event(beat=beat, dur=dur, midi=midi_of(degree, octave),
                 finger=finger, slur=slur, tie=tie)


def bass_fig(root, octaves, fingers=(5, 2, 1, 2, 1, 2, 1, 2), thirds=2):
    """左手固定分解和弦音型：根 五 根 三 五 三 根 五（十六分音符）。"""
    order = [0, 4, 0, thirds, 4, thirds, 0, 4]
    evs = []
    for i, step in enumerate(order):
        deg = ((root - 1 + step) % 7) + 1
        carry = (root - 1 + step) // 7
        evs.append(Event(beat=i * 0.25, dur=0.25,
                         midi=midi_of(deg, octaves[i] + carry),
                         finger=fingers[i]))
    return evs


def build() -> Score:
    ms = []

    # ---- 1-3 小节 ----
    m = Measure(number=1, chord="C")
    m.melody = [mel(0, .5, 5, 0, 1), mel(.5, .5, 3, 1, 5, slur=True),
                mel(1, .5, 2, 1, 4), mel(1.5, .5, 3, 1, 5)]
    m.bass = bass_fig(1, [-1, -1, 0, 0, 0, 0, 0, -1])
    ms.append(m)

    m = Measure(number=2, chord="C")
    m.melody = [mel(0, 1.5, 1, 1, 3), mel(1.5, .25, 7, 0, 4), mel(1.75, .25, 6, 0, 3)]
    m.bass = bass_fig(1, [-1, -1, 0, 0, 0, 0, 0, -1])
    ms.append(m)

    m = Measure(number=3, chord="Em")
    m.melody = [mel(0, .75, 3, 0, 1), mel(.75, .25, 4, 0, 2),
                mel(1, .5, 5, 0, 3), mel(1.5, .5, 6, 0, 4)]
    m.bass = bass_fig(3, [-2, -2, -1, -1, -1, -1, -1, -2])
    ms.append(m)

    # ---- 4-6 小节 ----
    m = Measure(number=4, chord="Em")
    m.melody = [mel(0, 2, 5, 0, 3)]
    m.bass = bass_fig(3, [-2, -2, -1, -1, -1, -1, -1, -2])
    ms.append(m)

    m = Measure(number=5, chord="F")
    m.melody = [mel(0, .5, 6, 0, 1), mel(.5, .5, 7, 0, 2),
                mel(1, .5, 1, 1, 3), mel(1.5, .5, 6, 0, 1)]
    m.bass = bass_fig(4, [-2, -1, -1, -1, 0, -1, -1, -1])
    ms.append(m)

    m = Measure(number=6, chord="F")
    m.melody = [mel(0, .5, 4, 1, 1), mel(.5, .5, 3, 1, 2), mel(1, .5, 6, 0, 1),
                mel(1.5, .25, 7, 0, 2), mel(1.75, .25, 1, 1, 3)]
    m.bass = bass_fig(4, [-2, -1, -1, -1, 0, -1, -1, -1])
    ms.append(m)

    # ---- 7-9 小节 ----
    m = Measure(number=7, chord="G")
    m.melody = [mel(0, 2, 2, 1, 4, tie=True)]
    m.bass = bass_fig(5, [-2, -1, -1, -1, 0, -1, -1, -1])
    ms.append(m)

    m = Measure(number=8, chord="G")
    m.melody = [mel(0, 2, 2, 1)]
    m.bass = bass_fig(5, [-2, -1, -1, -1, 0, -1, -1, -1])
    ms.append(m)

    m = Measure(number=9, chord="Am")
    m.melody = [mel(0, .5, 3, 1, 3), mel(.5, .5, 5, 1, 5), mel(1, .5, 2, 1, 2),
                mel(1.5, .25, 3, 1, 3), mel(1.75, .25, 4, 1, 4)]
    m.bass = bass_fig(1, [-1, -1, 0, 0, 0, 0, 0, -1])
    ms.append(m)

    # ---- 10-12 小节 ----
    m = Measure(number=10, chord="C")
    m.melody = [mel(0, 1.5, 3, 1, 3), mel(1.5, .5, 1, 1, 2)]
    m.bass = bass_fig(1, [-1, -1, 0, 0, 0, 0, 0, -1])
    ms.append(m)

    m = Measure(number=11, chord="Em")
    m.melody = [mel(0, .5, 7, 0, 1), mel(.5, .5, 2, 1, 4),
                mel(1, .5, 1, 1, 3), mel(1.5, .5, 7, 0, 2)]
    m.bass = bass_fig(3, [-2, -2, -1, -1, -1, -1, -1, -2])
    ms.append(m)

    m = Measure(number=12, chord="Am")
    m.melody = [mel(0, 2, 6, 0, 1)]
    m.bass = bass_fig(6, [-2, -1, -1, 0, 0, 0, -1, -2])
    ms.append(m)

    return Score(title="花又落", tonic_pc=0, mode="major", beats=2, unit=4,
                 tempo=72.0, measures=ms, measures_per_system=3,
                 systems_per_page=4)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    out = os.path.join(root, "out")
    os.makedirs(out, exist_ok=True)
    score = build()
    r = Renderer()
    pages = r.render_all(score)
    p = os.path.join(out, "golden_p1.png")
    pages[0].save(p)

    ref_path = os.path.join(root, "ref", "reference.jpg")
    if os.path.exists(ref_path):
        ref = Image.open(ref_path).convert("RGB")
        w = 1080
        canvas = Image.new("RGB", (w * 2 + 20, 1440), "white")
        canvas.paste(ref.resize((w, 1440)), (0, 0))
        canvas.paste(pages[0].resize((w, 1440)), (w + 20, 0))
        cmp_p = os.path.join(out, "compare.png")
        canvas.resize((w + 10, 720)).save(cmp_p)
        print("对比图:", cmp_p)
    print("渲染:", p, pages[0].size)


if __name__ == "__main__":
    main()
