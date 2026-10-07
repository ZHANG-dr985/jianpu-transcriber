"""命令行入口：MP3 -> 简谱。

用法示例：
  python cli.py --input song.mp3 --outdir out --title "花又落" --key auto --meter 2/4
  python cli.py --render-text out/song.jianpu.txt --outdir out2
输出：标准输出打印 JSON（GUI 也依赖该格式，请勿随意改动键名）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jianpu import export as EX
from jianpu.audio import find_ffmpeg
from jianpu.model import Score
from jianpu.render import Renderer
from jianpu.transcribe import Options, analyze

KEY_MAP = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5,
           "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11}
NOTE_MAP = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def parse_key(v: str):
    v = (v or "auto").strip()
    if v.lower() == "auto":
        return "auto"
    u = v.upper().replace("\u266d", "B").replace("\u266f", "#").replace("B#", "B")
    if u in KEY_MAP:
        return KEY_MAP[u]
    try:
        return int(v) % 12
    except ValueError:
        return "auto"


def parse_note(v: str) -> Optional[int]:
    v = v.strip()
    if not v:
        return None
    letter = v[0].upper()
    if letter not in NOTE_MAP:
        return None
    rest = v[1:]
    semi = 0
    while rest and rest[0] in "#b\u266f\u266d":
        semi += 1 if rest[0] in "#\u266f" else -1
        rest = rest[1:]
    octave = 4
    if rest:
        try:
            octave = int(rest)
        except ValueError:
            octave = 4
    return 12 * (octave + 1) + NOTE_MAP[letter] + semi


def parse_range(v: str):
    if not v:
        return None
    parts = v.replace("–", "-").split("-")
    if len(parts) != 2:
        return None
    a, b = parse_note(parts[0]), parse_note(parts[1])
    if a is None or b is None:
        return None
    return min(a, b), max(a, b)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="MP3 扒简谱（88 键电子琴指法版）")
    p.add_argument("--input", "-i", help="输入音频（mp3/wav/m4a/flac…）")
    p.add_argument("--outdir", "-o", default=None, help="输出目录")
    p.add_argument("--title", default=None, help="曲名（默认取文件名）")
    p.add_argument("--key", default="auto", help="调性：auto 或 C/D/bB…")
    p.add_argument("--meter", default="2/4", help="拍号：auto / 2/4 / 3/4 / 4/4")
    p.add_argument("--bpm", default="auto", help="速度：auto 或数字")
    p.add_argument("--melody-range", default="G3-E6", help="主旋律音域，如 G3-E6")
    p.add_argument("--sensitivity", type=float, default=0.5, help="灵敏度 0..1")
    p.add_argument("--no-bass", action="store_true", help="不生成左手伴奏")
    p.add_argument("--engine", default="viterbi", choices=["viterbi", "onset"],
                   help="主旋律提取算法：viterbi（连续跟踪）/ onset（音头分段）")
    p.add_argument("--tempo-prefer", default="slow", choices=["slow", "fast", "none"],
                   help="速度倍率倾向：slow 偏向较慢读法（默认）/ fast / none")
    p.add_argument("--python", default=None, help=argparse.SUPPRESS)
    p.add_argument("--measures-per-line", type=int, default=3, help="每行小节数")
    p.add_argument("--systems-per-page", type=int, default=4, help="每页行数")
    p.add_argument("--formats", default="png,pdf,txt,midi", help="输出格式")
    p.add_argument("--max-seconds", type=float, default=300.0, help="只分析前 N 秒")
    p.add_argument("--render-text", default=None, help="渲染已有的 .jianpu.txt")
    p.add_argument("--dpi", type=int, default=150)
    return p


def run(argv=None) -> Dict:
    args = build_parser().parse_args(argv)
    result: Dict = {"ok": False, "message": "", "files": {}, "outdir": ""}
    formats = {f.strip().lower() for f in (args.formats or "").split(",") if f.strip()}

    # ---- 文本重新排版 ----
    if args.render_text:
        src = args.render_text
        if not os.path.exists(src):
            result["message"] = f"找不到文本文件：{src}"
            return result
        with open(src, encoding="utf-8") as f:
            score = EX.load_text(f.read())
        EX.fix_beats(score)
        outdir = args.outdir or os.path.dirname(os.path.abspath(src))
        base = os.path.splitext(os.path.basename(src))[0]
        # 从「xxx.jianpu.txt」重排版时，避免输出成 xxx.jianpu_1.png
        for suffix in (".jianpu", ".简谱"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
    else:
        if not args.input:
            result["message"] = "缺少 --input 音频文件"
            return result
        if not os.path.exists(args.input):
            result["message"] = f"找不到音频文件：{args.input}"
            return result
        if not find_ffmpeg():
            result["message"] = "未找到 ffmpeg，无法解码 MP3（请安装 ffmpeg 后重试）"
            return result
        outdir = args.outdir or os.path.join(os.path.dirname(os.path.abspath(args.input)),
                                             "简谱输出")
        base = os.path.splitext(os.path.basename(args.input))[0]
        title = args.title or base
        rng = parse_range(args.melody_range) or (55, 88)
        opt = Options(title=title, key=parse_key(args.key), meter=args.meter,
                      bpm=args.bpm, melody_low=rng[0], melody_high=rng[1],
                      with_bass=not args.no_bass,
                      measures_per_system=max(1, args.measures_per_line),
                      systems_per_page=max(1, args.systems_per_page),
                      max_seconds=args.max_seconds, sensitivity=args.sensitivity,
                      engine=args.engine, tempo_prefer=args.tempo_prefer)
        score, info = analyze(args.input, opt)
        if not info.ok:
            result["message"] = info.message
            result["key"] = info.key
            result["bpm"] = info.bpm
            return result
        result.update({"key": info.key, "bpm": info.bpm, "meter": info.meter,
                       "measures": info.measures, "notes": info.melody_notes,
                       "duration": info.duration, "elapsed": info.elapsed,
                       "key_confidence": info.key_confidence,
                       "warnings": info.warnings})

    os.makedirs(outdir, exist_ok=True)
    files: Dict[str, object] = {}
    renderer = Renderer()
    pages = renderer.render_all(score)

    if "png" in formats or "pdf" in formats:
        png_paths = [os.path.join(outdir, f"{base}_{i + 1}.png") for i in range(len(pages))]
        if "png" in formats:
            for img, p in zip(pages, png_paths):
                img.save(p, "PNG")
            files["png"] = png_paths
        if "pdf" in formats:
            pdf = os.path.join(outdir, f"{base}.pdf")
            pages[0].save(pdf, "PDF", resolution=float(args.dpi),
                          save_all=True, append_images=pages[1:])
            files["pdf"] = pdf

    if "txt" in formats:
        t1 = os.path.join(outdir, f"{base}.jianpu.txt")
        t2 = os.path.join(outdir, f"{base}.对照表.txt")
        with open(t1, "w", encoding="utf-8") as f:
            f.write(EX.dump_text(score))
        with open(t2, "w", encoding="utf-8") as f:
            f.write(EX.human_text(score))
        files["text"] = t1
        files["table"] = t2

    if "midi" in formats:
        m = os.path.join(outdir, f"{base}.mid")
        EX.export_midi(score, m)
        files["midi"] = m

    result["ok"] = True
    result["files"] = files
    result["outdir"] = outdir
    result["preview"] = (files.get("png") or [None])[0]
    if not result.get("title"):
        result["title"] = score.title
    return result


def main():
    res = run()
    sys.stdout.write(json.dumps(res, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
