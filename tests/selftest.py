"""自检：版式一致性 / 端到端 / 文本往返 / MIDI / 指法。

运行: python tests/selftest.py
全部通过时退出码 0。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import wave

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import golden  # noqa: E402
from jianpu import export as EX  # noqa: E402
from jianpu.fingering import rh_fingering  # noqa: E402
from jianpu.render import Renderer  # noqa: E402

PY = sys.executable
REF = os.path.join(ROOT, "ref", "reference.jpg")
OUT = os.path.join(ROOT, "out")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"   {detail}" if detail else ""))


def ink(img, thr=170):
    return np.array(img.convert("L")) < thr


def vertical_lines(m, y0, y1, minfrac=0.85):
    cnt = m[y0:y1, :].sum(0)
    need = (y1 - y0) * minfrac
    xs = [x for x in range(m.shape[1]) if cnt[x] >= need]
    out, s, p = [], None, None
    for x in xs:
        if s is None:
            s = p = x
        elif x - p <= 2:
            p = x
        else:
            out.append((s + p) / 2)
            s = p = x
    if s is not None:
        out.append((s + p) / 2)
    return out


def xruns(m, y0, y1, x0=0, x1=1080, minw=2):
    r = m[y0:y1, x0:x1].any(0)
    out, s = [], None
    for i, v in enumerate(r):
        if v and s is None:
            s = i
        elif not v and s is not None:
            if i - s >= minw:
                out.append(((x0 + s + x0 + i - 1) / 2))
            s = None
    if s is not None:
        out.append((x0 + s + x1 - 1) / 2)
    return out


# ------------------------------------------------------------------ 1 版式
def test_layout():
    print("\n[1] 版式与参考图一致性")
    score = golden.build()
    page = Renderer().render_all(score)[0]
    check("页面尺寸 1080x1440", page.size == (1080, 1440), str(page.size))
    m = ink(page)
    ref = ink(Image.open(REF)) if os.path.exists(REF) else None
    lines = vertical_lines(m, 250, 390)
    check("第一行小节线位置", len(lines) >= 4 and
          all(abs(a - b) < 2.5 for a, b in zip(lines, [54.5, 400.5, 713.0, 1024.5])),
          str([round(x, 1) for x in lines]))
    mel = xruns(m, 250, 284, 100, 1030)
    want = [124, 193, 262.5, 331.5]
    got = [x for x in mel if x < 400][:4]
    check("第1小节旋律数字位置", len(got) == 4 and all(abs(a - b) < 3 for a, b in zip(got, want)),
          str([round(x, 1) for x in got]))
    # 减时线
    beam = [y for y in range(278, 296) if m[y, 120:340].sum() > 100]
    check("减时线 y=284,285", beam[:2] == [284, 285], str(beam))
    if ref is not None:
        # 与参考图的整体墨量接近
        ratio = m.mean() / max(1e-9, ref.mean())
        check("墨量比例接近参考图 (±12%)", 0.88 < ratio < 1.12, f"{ratio:.3f}")


# ------------------------------------------------------------------ 2 指法
def test_fingering():
    print("\n[2] 指法算法（对照参考图实际指法）")
    # 参考图第 1、3 小节右手实际指法：①⑤④⑤ / ①②③④
    exact = [
        ("5 3' 2' 3'", [67, 76, 74, 76], [1, 5, 4, 5]),
        ("3 4 5 6", [64, 65, 67, 69], [1, 2, 3, 4]),
    ]
    ok = True
    for name, midis, expect in exact:
        got = rh_fingering(midis)
        if got != expect:
            ok = False
            print(f"      {name}: 期望 {expect} 实得 {got}")
    check("右手指法与参考图 ①⑤④⑤ / ①②③④ 一致", ok)

    # 其它旋律：只需合法、可用（1..5、跨度合理、无同指重复不同音）
    samples = [[69, 71, 72, 69], [71, 74, 72, 71], [60, 62, 64, 65, 67],
               [72, 71, 69, 67, 65], [67, 69, 71, 72, 71, 69]]
    valid = True
    for s in samples:
        f = rh_fingering(s)
        if len(f) != len(s) or any(x not in (1, 2, 3, 4, 5) for x in f):
            valid = False
            print(f"      {s} -> {f} 非法")
            continue
        for i in range(1, len(s)):
            if f[i] == f[i - 1] and s[i] != s[i - 1]:
                valid = False
                print(f"      {s} -> {f} 相邻不同音用了同一指")
    check("其它旋律指法合法可用", valid)


# ------------------------------------------------------------------ 3 端到端
def ensure_test_audio():
    mp3 = os.path.join(OUT, "test_song.mp3")
    if not os.path.exists(mp3):
        subprocess.run([PY, os.path.join(HERE, "make_test_audio.py")], cwd=ROOT, check=True)
    return mp3


def test_end_to_end():
    print("\n[3] 端到端（合成 MP3 -> 简谱）")
    mp3 = ensure_test_audio()
    outdir = os.path.join(OUT, "selftest")
    cmd = [PY, os.path.join(ROOT, "cli.py"), "--input", mp3, "--outdir", outdir,
           "--title", "花又落", "--formats", "png,pdf,txt,midi"]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, env=env)
    line = [l for l in p.stdout.decode("utf-8", "replace").splitlines() if l.strip()]
    res = json.loads(line[-1]) if line else {}
    check("CLI ok=true", res.get("ok") is True, res.get("message", ""))
    check("调性识别为 C", res.get("key") == "C", str(res.get("key")))
    check("速度识别接近 72", abs(float(res.get("bpm", 0)) - 72) < 8, str(res.get("bpm")))
    check("小节数 12", res.get("measures") == 12, str(res.get("measures")))
    png = (res.get("files") or {}).get("png") or []
    check("生成 PNG", png and os.path.exists(png[0]), str(png[:1]))
    check("生成 PDF", os.path.exists((res.get("files") or {}).get("pdf", "")),
          str((res.get("files") or {}).get("pdf")))
    check("生成 MIDI", os.path.exists((res.get("files") or {}).get("midi", "")))
    check("生成简谱文本", os.path.exists((res.get("files") or {}).get("text", "")))

    # 主旋律还原度：与真实谱比较音高序列（允许少量漏检/多检）
    truth = [e.midi for m in golden.build().measures for e in m.melody]
    text = open((res.get("files") or {}).get("text"), encoding="utf-8").read()
    sc = EX.load_text(text)
    got = [e.midi for m in sc.measures for e in m.melody]
    inter = 0
    used = set()
    for t in truth:
        for i, g in enumerate(got):
            if i not in used and g == t:
                used.add(i)
                inter += 1
                break
    rec = inter / max(1, len(truth))
    check("主旋律音高召回率 ≥ 60%", rec >= 0.60, f"{rec:.2%} ({inter}/{len(truth)})")


# ------------------------------------------------------------------ 4 文本往返
def test_text_roundtrip():
    print("\n[4] 文本往返 / MIDI 文件格式")
    score = golden.build()
    txt = EX.dump_text(score)
    back = EX.load_text(txt)
    check("往返后小节数一致", len(back.measures) == len(score.measures),
          f"{len(back.measures)} vs {len(score.measures)}")
    a = [(round(e.beat, 3), e.dur, e.midi) for m in score.measures for e in m.melody]
    b = [(round(e.beat, 3), e.dur, e.midi) for m in back.measures for e in m.melody]
    check("往返后旋律完全一致", a == b, "" if a == b else f"{len(a)} vs {len(b)} 音符")
    ab = [(round(e.beat, 3), e.dur, e.midi, e.finger) for m in score.measures for e in m.bass]
    bb = [(round(e.beat, 3), e.dur, e.midi, e.finger) for m in back.measures for e in m.bass]
    check("往返后伴奏完全一致", ab == bb)
    mid = os.path.join(OUT, "selftest_roundtrip.mid")
    EX.export_midi(back, mid)
    head = open(mid, "rb").read(4)
    check("MIDI 文件头正确", head == b"MThd", str(head))
    check("MIDI 体积合理", os.path.getsize(mid) > 400, str(os.path.getsize(mid)))


# ------------------------------------------------------------------ 5 参数
def test_options():
    print("\n[5] 命令行参数行为")
    mp3 = ensure_test_audio()
    outdir = os.path.join(OUT, "selftest2")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # 手动指定调性与速度
    cmd = [PY, os.path.join(ROOT, "cli.py"), "--input", mp3, "--outdir", outdir,
           "--key", "D", "--bpm", "72", "--meter", "3/4", "--no-bass",
           "--engine", "onset", "--measures-per-line", "4", "--formats", "png"]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, env=env)
    line = [l for l in p.stdout.decode("utf-8", "replace").splitlines() if l.strip()]
    res = json.loads(line[-1]) if line else {}
    check("手动参数模式 ok=true", res.get("ok") is True, res.get("message", ""))
    check("调性跟随手动设置(D)", res.get("key") == "D", str(res.get("key")))
    check("速度跟随手动设置(72)", abs(float(res.get("bpm", 0)) - 72) < 0.01, str(res.get("bpm")))
    check("拍号跟随手动设置(3/4)", res.get("meter") == "3/4", str(res.get("meter")))
    # 缺失文件
    p = subprocess.run([PY, os.path.join(ROOT, "cli.py"), "--input", "no_such_file.mp3"],
                       cwd=ROOT, capture_output=True, env=env)
    line = [l for l in p.stdout.decode("utf-8", "replace").splitlines() if l.strip()]
    res = json.loads(line[-1]) if line else {}
    check("缺失文件时给出中文错误且 ok=false",
          res.get("ok") is False and "找不到" in (res.get("message") or ""),
          res.get("message", ""))


def main():
    os.makedirs(OUT, exist_ok=True)
    print("=" * 72)
    print("简谱扒谱工具 自检")
    print("=" * 72)
    test_layout()
    test_fingering()
    test_end_to_end()
    test_text_roundtrip()
    test_options()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 72)
    print(f"共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败项：")
        for n in failed:
            print("  - " + n)
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
