"""调参用：对比检测到的旋律与真实旋律。"""
from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from jianpu import audio as A  # noqa: E402
from jianpu.model import midi_to_name  # noqa: E402
from golden import build  # noqa: E402

PATH = os.path.join(os.path.dirname(HERE), "out", "test_song.mp3")


def truth():
    score = build()
    beat = 60.0 / score.tempo
    out = []
    t = 0.5
    for m in score.measures:
        for e in m.melody:
            out.append((t + e.beat * beat, t + (e.beat + e.dur) * beat, e.midi))
        t += score.beats * beat
    return out


def main():
    bpm_force = float(sys.argv[1]) if len(sys.argv) > 1 else 72.0
    x = A.decode_audio(PATH)
    mag = A.stft_mag(x)
    M = A.build_salience_matrix(A.SR, A.N_FFT, harmonics=6)
    sal = A.salience(mag, M)
    tonic, mode, kc = A.estimate_key(sal)
    print("key:", tonic, mode, round(kc, 3))
    onset = A.onset_envelope(mag)
    bpm, phase, conf = A.estimate_tempo(onset)
    print("auto bpm:", round(bpm, 2), "phase", round(phase, 3), "conf", round(conf, 2))

    # 打印自相关峰
    xx = onset - onset.mean()
    n = xx.size
    size = 1
    while size < 2 * n:
        size *= 2
    ac = np.fft.irfft(np.fft.rfft(xx, size) * np.conj(np.fft.rfft(xx, size)), size)[:n].real
    ac[0] = 0
    bpms = []
    for b in (48, 60, 72, 90, 100, 110, 120, 128, 144, 160, 180):
        lag = int(round(60.0 / b * A.FPS))
        bpms.append((b, round(float(ac[lag] / ac.max()), 3)))
    print("归一化自相关:", bpms)

    fps = A.FPS
    path = A.viterbi_melody(sal, midi_low=A.MIDI_LOW, midi_high=A.MIDI_HIGH,
                            prefer_low=55, prefer_high=88)
    path = A.smooth_path(path, 5)
    confv = A.melody_confidence(sal, path)
    notes = A.segment_notes(path, confv, min_conf=0.05)
    print(f"检测音符数 {len(notes)}  真实 {len(truth())}")
    print(f"{'真实(时间/音)':<34}{'检测(时间/音)'}")
    tr = truth()
    for i in range(max(len(tr), len(notes))):
        a = f"{tr[i][0]:6.2f}-{tr[i][1]:6.2f} {midi_to_name(tr[i][2]):>4}" if i < len(tr) else ""
        b = (f"{notes[i].start:6.2f}-{notes[i].end:6.2f} "
             f"{midi_to_name(notes[i].midi):>4} c={notes[i].conf:.2f}") if i < len(notes) else ""
        print(f"{a:<34}{b}")


if __name__ == "__main__":
    main()
