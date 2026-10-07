"""用黄金样板合成一段 MP3 测试音频，做端到端验证。

运行: python tests/make_test_audio.py
输出: out/test_song.wav / out/test_song.mp3
"""
from __future__ import annotations

import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from golden import build  # noqa: E402

SR = 22050


def add_note(buf, midi, t0, dur, amp, decay=3.2, harmonics=(1.0, 0.45, 0.28, 0.12, 0.06)):
    f0 = 440.0 * 2 ** ((midi - 69) / 12.0)
    n = int(dur * SR) + int(0.25 * SR)
    t = np.arange(n) / SR
    env = np.exp(-decay * t) * (1 - np.exp(-t * 400))
    sig = np.zeros(n)
    for h, a in enumerate(harmonics, start=1):
        if f0 * h > SR / 2 - 100:
            break
        sig += a * np.sin(2 * np.pi * f0 * h * t + 0.3 * h)
    sig *= env * amp
    s = int(t0 * SR)
    e = min(buf.size, s + n)
    if e > s:
        buf[s:e] += sig[:e - s]


def main():
    score = build()
    beat = 60.0 / score.tempo
    total_beats = 1.0 + len(score.measures) * score.beats
    buf = np.zeros(int(total_beats * beat * SR) + SR)
    offset = 0.5
    for m in score.measures:
        for e in m.melody:
            if e.midi is not None:
                add_note(buf, e.midi, offset + e.beat * beat, e.dur * beat * 0.98, 0.30)
        for e in m.bass:
            if e.midi is not None:
                add_note(buf, e.midi, offset + e.beat * beat, e.dur * beat * 0.95, 0.18,
                         decay=6.0)
        offset += score.beats * beat
    peak = np.max(np.abs(buf))
    if peak > 0:
        buf = buf / peak * 0.85
    buf += np.random.default_rng(0).normal(0, 0.0015, buf.size)
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(os.path.dirname(here), "out")
    os.makedirs(out, exist_ok=True)
    wav = os.path.join(out, "test_song.wav")
    import wave
    with wave.open(wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((buf * 32767).astype("<i2").tobytes())
    mp3 = os.path.join(out, "test_song.mp3")
    ff = None
    for c in ("ffmpeg", r"C:\ffmpeg\bin\ffmpeg.exe"):
        from shutil import which
        ff = which(c) if os.path.basename(c) == c else (c if os.path.exists(c) else None)
        if ff:
            break
    if ff:
        subprocess.run([ff, "-y", "-v", "error", "-i", wav, "-b:a", "192k", mp3], check=True)
    print("WAV:", wav)
    print("MP3:", mp3 if os.path.exists(mp3) else "（未生成，使用 WAV 测试）")
    print("真实参数: key=C  meter=2/4  tempo=%.0f  measures=%d" %
          (score.tempo, len(score.measures)))


if __name__ == "__main__":
    main()
