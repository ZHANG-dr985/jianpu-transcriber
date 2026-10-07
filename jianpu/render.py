"""简谱排版渲染器（Pillow）。

排版严格对照参考图《花又落》第 1 页逐像素测量所得：
    * 页面 1080x1440（等效 A4 纵向），白底黑字
    * 标题居中（宋体 52px），左上 "1=C"
    * 每行 3 小节，每页 4 行大谱表（高音=主旋律、低音=左手伴奏）
    * 左侧花括号 + 每谱表 2/4 拍号（仅第一行）
    * 数字上方圆圈指法、上下八度点、减时线（8/16 分）、附点、增时线 "-"
    * 每 3 小节左侧标 〔小节号〕、行首小节线、页脚 "n/N"
"""
from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from .model import (ACCIDENTAL_TEXT, Event, Measure, Rhythm, Score,
                    rhythm_of, scale_degree)

# ------------------------------------------------------------------ 版面常量
PAGE_W, PAGE_H = 1080, 1440

FONT_TIMES = r"C:\Windows\Fonts\times.ttf"
FONT_TIMES_BD = r"C:\Windows\Fonts\timesbd.ttf"
FONT_SONG = r"C:\Windows\Fonts\simsun.ttc"

TITLE_BASELINE = 87
TITLE_SIZE = 44
KEY_X, KEY_BASELINE, KEY_SIZE = 115, 185, 28

SYS_TOP0 = 241.0            # 第一行谱表顶部（行首小节线上端）
SYS_PITCH = 292.7           # 行距
B_OFF = 38.0                # 主旋律基线相对行顶的偏移

DIGIT_SIZE = 38             # 数字字号（实测字高 25px）
SMALL_SIZE = 22             # 变音记号
CIRCLE_R = 10.8             # 指法圆圈半径
CIRCLE_SIZE = 17            # 圈内数字
DOT_R = 3.0                 # 八度点 / 附点半径

MEL_BEAM1_DY = 5.5
MEL_BEAM2_DY = 10.5
BEAM_TH = 2.4
DASH_DY = -9.0
DASH_W, DASH_TH = 16.0, 3.0
RHYTHM_DOT_DY = -12.0

BASS_DY = 104.0             # 低音谱表数字基线相对 B
CIRCLE_DY = -50.5           # 指法圆圈中心相对本谱表数字基线
CIRCLE_RAISE = 8.0          # 有高八度点时圆圈上移
OCT_DOT_DY = -30.5          # 高八度点（第一颗）
OCT_DOT_LOW_DY = 18.0       # 低八度点（第一颗）
OCT_DOT_STEP = 8.4

LINE_TOP_DY = -38.0
LINE_BOT_DY = 117.0
BAR_W = 2.4
LEFT_BAR_X = 54.5
RIGHT_X = 1024.5
CONTENT_X0 = 56.0           # 第 2 行起的内容左边界
CONTENT_X0_FIRST = 90.0     # 第一行（右侧要让出拍号）
MEAS_MARGIN = 34.0          # 小节内首个音符的左边距
DIGIT_DX = 1.5              # Times 数字墨迹光学修正
MEAS_NUM_X = 53.0
MEAS_NUM_DY = -57.5
MEAS_NUM_SIZE = 21

PAGE_NUM_Y = 1376
PAGE_NUM_SIZE = 27

INK = (0, 0, 0)
CIRCLE_INK = (150, 150, 150)
CIRCLE_TEXT_INK = (90, 90, 90)
BEAM_EXT_L, BEAM_EXT_R = 10.0, 11.0


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        if path.lower().endswith(".ttc"):
            return ImageFont.truetype(path, size, index=0)
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


class Renderer:
    """把 Score 渲染成多页 PNG/PDF。"""

    def __init__(self, scale: float = 1.0):
        self.scale = scale
        self._fonts: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}

    def font(self, path: str, size: int) -> ImageFont.FreeTypeFont:
        key = (path, size)
        if key not in self._fonts:
            self._fonts[key] = _font(path, max(1, int(round(size * self.scale))))
        return self._fonts[key]

    # -------------------------------------------------------------- 页面
    def render_all(self, score: Score) -> List[Image.Image]:
        per_page = max(1, score.systems_per_page)
        per_sys = max(1, score.measures_per_system)
        total = len(score.measures)
        pages = max(1, math.ceil(total / (per_page * per_sys)))
        return [self.render_page(score, i) for i in range(pages)]

    def render_page(self, score: Score, page_index: int) -> Image.Image:
        s = self.scale
        img = Image.new("RGB", (int(PAGE_W * s), int(PAGE_H * s)), "white")
        d = ImageDraw.Draw(img)
        per_sys = max(1, score.measures_per_system)
        per_page = max(1, score.systems_per_page)
        first_measure_idx = page_index * per_page * per_sys
        page_count = max(1, math.ceil(len(score.measures) / (per_page * per_sys)))

        # 标题
        f_title = self.font(FONT_SONG, TITLE_SIZE)
        d.text((PAGE_W / 2 * s, TITLE_BASELINE * s), score.title or "未命名",
               font=f_title, fill=INK, anchor="ms")

        for row in range(per_page):
            start = first_measure_idx + row * per_sys
            if start >= len(score.measures):
                break
            measures = score.measures[start:start + per_sys]
            top = SYS_TOP0 + row * SYS_PITCH
            is_very_first = (start == 0)
            self._draw_system(d, score, measures, start, top, is_very_first)

        # 页码
        f_pn = self.font(FONT_TIMES, PAGE_NUM_SIZE)
        d.text((PAGE_W / 2 * s, PAGE_NUM_Y * s), f"{page_index + 1}/{page_count}",
               font=f_pn, fill=INK, anchor="ms")
        return img

    # -------------------------------------------------------------- 一行谱表
    def _draw_system(self, d: ImageDraw.ImageDraw, score: Score,
                     measures: Sequence[Measure], start_idx: int,
                     top: float, is_very_first: bool) -> None:
        s = self.scale
        B = top + B_OFF
        x0 = CONTENT_X0_FIRST if is_very_first else CONTENT_X0
        n = len(measures)
        width = (RIGHT_X - x0) / n

        # 花括号 + 行首小节线
        self._draw_brace(d, LEFT_BAR_X - 3.5, B + LINE_TOP_DY, B + LINE_BOT_DY)
        self._draw_vline(d, LEFT_BAR_X, B + LINE_TOP_DY, B + LINE_BOT_DY)

        if is_very_first:
            self._draw_key(d, score)
            self._draw_timesig(d, score, x=71.0, B=B)
            self._draw_timesig(d, score, x=71.0, B=B + BASS_DY)

        last_system_of_score = (start_idx + n) >= len(score.measures)
        for i, m in enumerate(measures):
            bx = x0 + i * width
            self._draw_measure(d, score, m, bx, width, B)
            # 小节线
            if i < n - 1:
                self._draw_vline(d, bx + width, B + LINE_TOP_DY, B + LINE_BOT_DY)
        # 行尾小节线
        if last_system_of_score:
            self._draw_vline(d, RIGHT_X, B + LINE_TOP_DY, B + LINE_BOT_DY, w=BAR_W)
            self._draw_vline(d, RIGHT_X + 7, B + LINE_TOP_DY, B + LINE_BOT_DY, w=6.0)
        else:
            self._draw_vline(d, RIGHT_X, B + LINE_TOP_DY, B + LINE_BOT_DY)

        # 〔小节号〕
        if start_idx != 0:
            self._draw_measure_number(d, measures[0].number, B)

    def _draw_key(self, d: ImageDraw.ImageDraw, score: Score) -> None:
        s = self.scale
        f = self.font(FONT_TIMES, KEY_SIZE)
        d.text((KEY_X * s, KEY_BASELINE * s), "1=" + score.key_label,
               font=f, fill=INK, anchor="ls")

    def _draw_timesig(self, d: ImageDraw.ImageDraw, score: Score,
                      x: float, B: float) -> None:
        s = self.scale
        f = self.font(FONT_TIMES, 32)
        num, den = str(score.beats), str(score.unit)
        d.text((x * s, (B - 17) * s), num, font=f, fill=INK, anchor="ms")
        d.text((x * s, (B + 11) * s), den, font=f, fill=INK, anchor="ms")
        d.line([( (x - 13.5) * s, (B - 14.5) * s), ((x + 13.5) * s, (B - 14.5) * s)],
               fill=INK, width=max(1, int(round(2 * s))))

    def _draw_brace(self, d: ImageDraw.ImageDraw, x_right: float,
                    y_top: float, y_bot: float) -> None:
        """左侧花括号：两段三次贝塞尔，尖端在中线左侧。"""
        s = self.scale
        y_mid = (y_top + y_bot) / 2.0
        x_tip = x_right - 12.0
        x_mid = x_tip + 0.62 * (x_right - x_tip)
        w = max(1, int(round(2.2 * s)))

        def bezier(p0, p1, p2, p3, steps=28):
            pts = []
            for i in range(steps + 1):
                t = i / steps
                mt = 1 - t
                x = (mt ** 3 * p0[0] + 3 * mt ** 2 * t * p1[0] +
                     3 * mt * t ** 2 * p2[0] + t ** 3 * p3[0])
                y = (mt ** 3 * p0[1] + 3 * mt ** 2 * t * p1[1] +
                     3 * mt * t ** 2 * p2[1] + t ** 3 * p3[1])
                pts.append((x * s, y * s))
            return pts

        top_pts = bezier((x_right, y_top), (x_mid, y_top),
                         (x_mid, y_mid), (x_tip, y_mid))
        bot_pts = bezier((x_tip, y_mid), (x_mid, y_mid),
                         (x_mid, y_bot), (x_right, y_bot))
        d.line(top_pts, fill=INK, width=w, joint="curve")
        d.line(bot_pts, fill=INK, width=w, joint="curve")

    def _draw_vline(self, d: ImageDraw.ImageDraw, x: float, y0: float, y1: float,
                    w: float = BAR_W) -> None:
        s = self.scale
        d.line([(x * s, y0 * s), (x * s, y1 * s)], fill=INK,
               width=max(1, int(round(w * s))))

    def _draw_measure_number(self, d: ImageDraw.ImageDraw, number: int, B: float) -> None:
        """〔n〕：细方括号（手工绘制，与参考图一致）。"""
        s = self.scale
        cy = B + MEAS_NUM_DY
        half = 8.0
        tick = 4.0
        w = max(1, int(round(1.6 * s)))
        for bx, sgn in ((MEAS_NUM_X - 9.5, 1), (MEAS_NUM_X + 9.5, -1)):
            y0, y1 = cy - half, cy + half
            d.line([((bx + sgn * tick) * s, y0 * s), (bx * s, y0 * s),
                    (bx * s, y1 * s), ((bx + sgn * tick) * s, y1 * s)],
                   fill=INK, width=w, joint="curve")
        f = self.font(FONT_TIMES, MEAS_NUM_SIZE)
        d.text((MEAS_NUM_X * s, (cy + 0.5) * s), str(number), font=f,
               fill=INK, anchor="mm")

    # -------------------------------------------------------------- 一小节
    def _measure_grid(self, bx: float, width: float, score: Score) -> Tuple[float, float]:
        ppq = (width - MEAS_MARGIN) / max(1, score.beats)
        return bx + MEAS_MARGIN, ppq

    def _x_of(self, beat: float, x0: float, ppq: float) -> float:
        return x0 + beat * ppq + DIGIT_DX

    def _draw_measure(self, d: ImageDraw.ImageDraw, score: Score, m: Measure,
                      bx: float, width: float, B: float) -> None:
        x0, ppq = self._measure_grid(bx, width, score)
        mel_pts = [(self._x_of(e.beat, x0, ppq), e) for e in sorted(m.melody, key=lambda e: e.beat)]
        bass_pts = [(self._x_of(e.beat, x0, ppq), e) for e in sorted(m.bass, key=lambda e: e.beat)]
        self._draw_staff(d, score, mel_pts, B, x0, ppq, is_melody=True)
        self._draw_staff(d, score, bass_pts, B + BASS_DY, x0, ppq, is_melody=False)

    def _draw_staff(self, d: ImageDraw.ImageDraw, score: Score,
                    pts: List[Tuple[float, Event]], baseline: float,
                    x0: float, ppq: float, is_melody: bool) -> None:
        for x, ev in pts:
            ev.recalc(score.tonic_pc)
            self._draw_note(d, score, ev, x, baseline, x0, ppq)
        # 减时线
        for level, x1, x2 in self._beams([(x, e) for x, e in pts], score):
            dy = MEL_BEAM1_DY if level == 1 else MEL_BEAM2_DY
            self._line_h(d, x1 - BEAM_EXT_L, x2 + BEAM_EXT_R,
                         baseline + dy, BEAM_TH)
        # 连音线 / 延音线
        for x1, x2, long_span in self._slurs([(x, e) for x, e in pts]):
            self._draw_slur(d, x1, x2, baseline, long_span, is_melody)

    def _draw_note(self, d: ImageDraw.ImageDraw, score: Score, ev: Event,
                   x: float, baseline: float, x0: float, ppq: float) -> None:
        s = self.scale
        if ev.is_rest:
            f = self.font(FONT_TIMES, DIGIT_SIZE)
            d.text((x * s, baseline * s), "0", font=f, fill=INK, anchor="ms")
            return
        degree, acc, octave = scale_degree(ev.midi, score.tonic_pc)
        rh = rhythm_of(ev.dur)

        # 指法圆圈
        if ev.finger:
            cy = baseline + CIRCLE_DY - (CIRCLE_RAISE if octave > 0 else 0.0)
            self._draw_finger(d, ev.finger, x, cy)

        # 数字
        f = self.font(FONT_TIMES, DIGIT_SIZE)
        d.text((x * s, baseline * s), str(degree), font=f, fill=INK, anchor="ms")
        # 变音记号
        if acc:
            fa = self.font(FONT_TIMES, SMALL_SIZE)
            d.text(((x - 13) * s, (baseline - 2) * s), ACCIDENTAL_TEXT[acc],
                   font=fa, fill=INK, anchor="ms")
        # 附点
        if rh.dots:
            d.ellipse([((x + 20) * s, (baseline + RHYTHM_DOT_DY - DOT_R) * s),
                       ((x + 20 + 2 * DOT_R) * s, (baseline + RHYTHM_DOT_DY + DOT_R) * s)],
                      fill=INK)
        # 八度点
        self._draw_octave_dots(d, x, baseline, octave)
        # 增时线 "-"
        for k in range(rh.dashes):
            dx = x0 + (ev.beat + 1 + k) * ppq
            d.line([((dx - DASH_W / 2) * s, (baseline + DASH_DY) * s),
                    ((dx + DASH_W / 2) * s, (baseline + DASH_DY) * s)],
                   fill=INK, width=max(1, int(round(DASH_TH * s))))

    def _draw_octave_dots(self, d: ImageDraw.ImageDraw, x: float, baseline: float,
                          octave: int) -> None:
        s = self.scale
        if octave > 0:
            for k in range(octave):
                cy = baseline + OCT_DOT_DY - k * OCT_DOT_STEP
                d.ellipse([((x - DOT_R) * s, (cy - DOT_R) * s),
                           ((x + DOT_R) * s, (cy + DOT_R) * s)], fill=INK)
        elif octave < 0:
            for k in range(-octave):
                cy = baseline + OCT_DOT_LOW_DY + k * OCT_DOT_STEP
                d.ellipse([((x - DOT_R) * s, (cy - DOT_R) * s),
                           ((x + DOT_R) * s, (cy + DOT_R) * s)], fill=INK)

    def _draw_finger(self, d: ImageDraw.ImageDraw, finger: int, x: float, cy: float) -> None:
        s = self.scale
        r = CIRCLE_R
        d.ellipse([((x - r) * s, (cy - r) * s), ((x + r) * s, (cy + r) * s)],
                  outline=CIRCLE_INK, width=max(1, int(round(1.4 * s))))
        f = self.font(FONT_TIMES, CIRCLE_SIZE)
        d.text((x * s, (cy + 0.5) * s), str(finger), font=f, fill=CIRCLE_TEXT_INK, anchor="ms")

    def _line_h(self, d: ImageDraw.ImageDraw, x1: float, x2: float, y: float,
                w: float) -> None:
        s = self.scale
        if x2 <= x1:
            return
        d.line([(x1 * s, y * s), (x2 * s, y * s)], fill=INK,
               width=max(1, int(round(w * s))))

    # -------------------------------------------------------------- 减时线
    @staticmethod
    def _beam_groups(pts: List[Tuple[float, Event]], beats: int) -> List[List[Tuple[float, Event]]]:
        items = [(x, e) for x, e in pts if not e.is_rest and rhythm_of(e.dur).underlines > 0]
        if not items:
            return []
        groups: List[List[Tuple[float, Event]]] = []
        all_eighths = all(rhythm_of(e.dur).underlines == 1 and rhythm_of(e.dur).dots == 0
                          for _, e in items)
        if all_eighths and len(items) > 2:
            return [items]
        cur: List[Tuple[float, Event]] = []
        cur_beat = None
        for x, e in items:
            b = int(math.floor(e.beat + 1e-6))
            if cur and b != cur_beat:
                groups.append(cur)
                cur = []
            cur.append((x, e))
            cur_beat = b
        if cur:
            groups.append(cur)
        return groups

    def _beams(self, pts: List[Tuple[float, Event]], score: Score
               ) -> List[Tuple[int, float, float]]:
        out: List[Tuple[int, float, float]] = []
        for g in self._beam_groups(pts, score.beats):
            xs = [x for x, _ in g]
            out.append((1, min(xs), max(xs)))
            deep = [(x, e) for x, e in g if rhythm_of(e.dur).underlines >= 2]
            if deep:
                dxs = [x for x, _ in deep]
                out.append((2, min(dxs), max(dxs)))
        return out

    # -------------------------------------------------------------- 连音线
    def _slurs(self, pts: List[Tuple[float, Event]]
               ) -> List[Tuple[float, float, bool]]:
        out = []
        for i, (x, e) in enumerate(pts):
            if (e.slur or e.tie) and i + 1 < len(pts):
                x2 = pts[i + 1][0]
                out.append((x, x2, (x2 - x) > 200))
        return out

    def _draw_slur(self, d: ImageDraw.ImageDraw, x1: float, x2: float,
                   baseline: float, long_span: bool, is_melody: bool) -> None:
        s = self.scale
        if x2 - x1 < 12:
            return
        if long_span:
            y_end = baseline - 47.0
            height = 30.0
        else:
            y_end = baseline - 84.0
            height = 16.0
        steps = 40
        pts = []
        xm = (x1 + x2) / 2.0
        for i in range(steps + 1):
            t = i / steps
            x = x1 + (x2 - x1) * t
            y = y_end - height * math.sin(math.pi * t)
            pts.append((x * s, y * s))
        d.line(pts, fill=INK, width=max(1, int(round(2.6 * s))), joint="curve")

    # -------------------------------------------------------------- 导出
    @staticmethod
    def save_png(pages: List[Image.Image], path: str) -> List[str]:
        base, ext = os.path.splitext(path)
        outs = []
        for i, img in enumerate(pages):
            p = f"{base}_{i + 1}.png" if len(pages) > 1 else f"{base}.png"
            img.save(p, "PNG")
            outs.append(p)
        return outs

    @staticmethod
    def save_pdf(pages: List[Image.Image], path: str) -> str:
        if not pages:
            raise ValueError("没有页面")
        first, rest = pages[0], pages[1:]
        first.save(path, "PDF", resolution=150.0, save_all=True, append_images=rest)
        return path
