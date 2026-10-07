# -*- coding: utf-8 -*-
"""生成应用图标：assets/jianpu.ico

图形：白底 + 黑色圆圈 + 圆圈中央黑色数字 "5"（简谱唱名 so）。
运行：
    D:\\PYTHON\\python.exe tools\\make_icon.py
输出：
    D:\\DH\\jianpu-transcriber\\assets\\jianpu.ico  （256/128/64/48/32/16 多尺寸）
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFont

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
ICON_PATH = os.path.join(ASSETS_DIR, "jianpu.ico")
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\times.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]
SIZES = [256, 128, 64, 48, 32, 16]
CANVAS = 256
SUPERSAMPLE = 4          # 先画 4 倍大再缩小，边缘更平滑


def load_font(px: int):
    """按优先级找一个可用字体。"""
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, px)
            except Exception:
                continue
    return ImageFont.load_default()


def make_image() -> Image.Image:
    """画一张 256x256 的 RGB 图。"""
    s = CANVAS * SUPERSAMPLE
    img = Image.new("RGB", (s, s), "white")
    d = ImageDraw.Draw(img)

    # 黑色圆圈（留出约 8% 边距）
    pad = int(s * 0.085)
    width = max(2, int(s * 0.055))
    d.ellipse([pad, pad, s - pad, s - pad], outline="black", width=width)

    # 中央数字 5（简谱里的“so”）
    font = load_font(int(s * 0.60))
    box = d.textbbox((0, 0), "5", font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text(((s - tw) / 2 - box[0], (s - th) / 2 - box[1]), "5", font=font, fill="black")

    return img.resize((CANVAS, CANVAS), Image.LANCZOS)


def main() -> int:
    os.makedirs(ASSETS_DIR, exist_ok=True)
    img = make_image()
    img.save(ICON_PATH, format="ICO", sizes=[(n, n) for n in SIZES])
    size = os.path.getsize(ICON_PATH)
    print("已生成图标：%s (%d bytes)" % (ICON_PATH, size))
    if size < 1024:
        print("警告：图标文件偏小，可能不完整。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
