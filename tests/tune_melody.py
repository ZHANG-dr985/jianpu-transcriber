"""参数搜索：以真实旋律为参照，评估主旋律提取准确率。"""
from __future__ import annotations

import itertools
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from jianpu import audio as A  # noqa: E402
from golden import build  # noqa: E402

PATH = os.path.join(os.path.dirname(HERE), "out", "test_song.mp3")


def truth_notes():
    score = build()
    beat = 60.0 / score.tempo
    out = []
    t = 0.5
    for m in score.measures:
        for e in m.melody:
            out.append((t + e.beat * beat, t + (e.beat + e.dur) * beat, e.midi))
        t += score.beats * beat
    return out


def score_path(path, conf, voicing, truth, min_conf=0.05):
    notes = A.segment_notes(path, conf, min_conf=min_conf)
    if voicing is not None:
        notes = [n for n in notes if voicing[int(n.start * A.FPS):max(int(n.start * A.FPS) + 1, int(n.end * A.FPS))].mean() > 0.4]
    total = sum(max(0.0, b - a) for a, b, _ in truth)
    good = 0.0
    for a, b, midi in truth:
        for nt in notes:
            lo, hi = max(a, nt.start), min(b, nt.end)
            if hi > lo and abs(nt.midi - midi) == 0:
                good += hi - lo
            elif hi > lo and abs(nt.midi - midi) == 12:
                good += 0.5 * (hi - lo)
    return good / max(1e-6, total), len(notes)


def run(sal, onset, truth, voicing=True, **kw):
    p = A.viterbi_melody(sal, midi_low=A.MIDI_LOW, midi_high=A.MIDI_HIGH,
                         prefer_low=55, prefer_high=88, onset=onset, **kw)
    p = A.smooth_path(p, 5)
    conf = A.melody_confidence(sal, p)
    v = A.melody_voicing(sal) if voicing else None
    return score_path(p, conf, v, truth)


def main():
    x = A.decode_audio(PATH)
    mag = A.stft_mag(x)
    M = A.build_salience_matrix(A.SR, A.N_FFT, harmonics=6)
    sal = A.salience(mag, M)
    onset = A.onset_envelope(mag)
    truth = truth_notes()
    grid = dict(emit=(1.5, 2.5, 4.0), jump=(0.1, 0.25), switch=(0.2, 0.5, 0.9),
                octave_penalty=(0.6, 1.5), slope=(0.0, 0.05, 0.10))
    keys = list(grid)
    rows = []
    for combo in itertools.product(*[grid[k] for k in keys]):
        kw = dict(zip(keys, combo))
        acc_v, n_v = run(sal, onset, truth, voicing=True, **kw)
        acc_n, n_n = run(sal, onset, truth, voicing=False, **kw)
        rows.append((acc_v, acc_n, kw, n_v, n_n))
    rows.sort(key=lambda r: -r[0])
    print(f"{'参数':<58}{'有声门限':<10}{'无门限':<10}{'音符数'}")
    for acc_v, acc_n, kw, n_v, n_n in rows[:20]:
        tag = ",".join(f"{k}={v}" for k, v in kw.items())
        print(f"{tag:<58}{acc_v:<10.3f}{acc_n:<10.3f}{n_v}/{n_n}")
    print("最优:", rows[0][2], "acc=%.3f" % rows[0][0])


if __name__ == "__main__":
    main()
