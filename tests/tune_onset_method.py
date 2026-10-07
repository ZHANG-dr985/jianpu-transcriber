"""对比两种主旋律提取法：Viterbi 连续跟踪 vs 起音区间主导音高。"""
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


def pick_peaks(env, min_gap=6, thr_ratio=0.25, fps=A.FPS):
    if env.size == 0:
        return np.array([], dtype=int)
    thr = max(env.max() * thr_ratio, 0.0)
    peaks = []
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


def melodic_onset_env(sal, mel_low=55, mel_high=88, midi_low=A.MIDI_LOW):
    mids = np.arange(sal.shape[0]) + midi_low
    band = (mids >= mel_low) & (mids <= mel_high)
    e = sal[band].sum(axis=0)
    e = np.log1p(e * 20.0)
    d = np.diff(e)
    d[d < 0] = 0
    d = np.concatenate([[0.0], d])
    k = np.ones(3, dtype=np.float32) / 3
    return np.convolve(d, k, mode="same")


def notes_by_onset(sal, mag, mel_low=55, mel_high=88, thr_ratio=0.25,
                   min_gap=6, attack_frac=0.65, min_len=6, voicing_rel=0.10,
                   skyline=0.0):
    """按起音切段，段内取主旋律音区的主导音高。

    skyline>0 时改用「最高显著峰」策略（旋律通常是最高声部）。
    """
    env = melodic_onset_env(sal, mel_low, mel_high)
    peaks = list(pick_peaks(env, min_gap=min_gap, thr_ratio=thr_ratio))
    T = sal.shape[1]
    if not peaks:
        return []
    bounds = peaks + [T]
    mids = np.arange(sal.shape[0]) + A.MIDI_LOW
    band = (mids >= mel_low) & (mids <= mel_high)
    ref = np.percentile(sal[band].max(axis=0), 90) + 1e-9
    out = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        if b - a < min_len:
            continue
        n_attack = max(3, int((b - a) * attack_frac))
        seg = sal[:, a:a + n_attack]
        prof = seg.mean(axis=1)
        prof[~band] = -1.0
        if skyline > 0:
            mx = float(prof.max())
            cand = np.where(prof >= skyline * mx)[0]
            m = int(cand.max()) if cand.size else int(np.argmax(prof))
        else:
            m = int(np.argmax(prof))
        if prof[m] < voicing_rel * ref:
            continue
        out.append((a / A.FPS, b / A.FPS, int(mids[m]), float(prof[m] / ref)))
    return out


def eval_events(events, truth):
    total = sum(max(0.0, b - a) for a, b, _ in truth)
    good = 0.0
    for a, b, midi in truth:
        for (s, e, m, c) in events:
            lo, hi = max(a, s), min(b, e)
            if hi > lo:
                if m == midi:
                    good += hi - lo
                elif abs(m - midi) == 12:
                    good += 0.5 * (hi - lo)
    return good / max(1e-6, total), len(events)


def main():
    x = A.decode_audio(PATH)
    mag = A.stft_mag(x)
    M = A.build_salience_matrix(A.SR, A.N_FFT, harmonics=6)
    sal = A.salience(mag, M)
    truth = truth_notes()
    print(f"{'参数':<52}{'准确率':<10}{'段数'}")
    best = None
    for thr in (0.10, 0.15, 0.2):
        for gap in (4, 6, 9):
            for att in (0.65, 1.0):
                for sky in (0.0, 0.5, 0.7, 0.88):
                    ev = notes_by_onset(sal, mag, thr_ratio=thr, min_gap=gap,
                                        attack_frac=att, skyline=sky)
                    acc, n = eval_events(ev, truth)
                    tag = f"thr={thr},gap={gap},att={att},sky={sky}"
                    print(f"{tag:<52}{acc:<10.3f}{n}")
                    if best is None or acc > best[0]:
                        best = (acc, tag, n, ev)
    print("\n最佳:", best[1], round(best[0], 3), best[2])
    print(f"\n{'真实':<28}{'检测':<28}")
    ev = best[3]
    for i in range(max(len(truth), len(ev))):
        a = f"{truth[i][0]:6.2f}-{truth[i][1]:6.2f} {truth[i][2]:>4}" if i < len(truth) else ""
        b = f"{ev[i][0]:6.2f}-{ev[i][1]:6.2f} {ev[i][2]:>4} c={ev[i][3]:.2f}" if i < len(ev) else ""
        print(f"{a:<28}{b}")


if __name__ == "__main__":
    main()
