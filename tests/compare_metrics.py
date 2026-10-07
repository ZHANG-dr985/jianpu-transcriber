"""把渲染结果与参考图做逐项像素对比，用于验证版式一致。

运行: python tests/compare_metrics.py out/golden_p1.png
"""
from __future__ import annotations

import sys
from typing import List, Tuple

import numpy as np
from PIL import Image

REF = r"D:\DH\jianpu-transcriber\ref\reference.jpg"


def load(p: str) -> np.ndarray:
    return np.array(Image.open(p).convert("L"))


def ink(img, thr=170):
    return img < thr


def col_runs(m, x: int, y0: int = 0, y1: int = 1440) -> List[Tuple[int, int]]:
    col = m[y0:y1, x]
    idx = np.where(col)[0] + y0
    if len(idx) == 0:
        return []
    runs, s, p = [], idx[0], idx[0]
    for y in idx[1:]:
        if y != p + 1:
            runs.append((int(s), int(p)))
            s = y
        p = y
    runs.append((int(s), int(p)))
    return runs


def xruns(m, y0: int, y1: int, x0: int = 0, x1: int = 1080, minw: int = 1):
    r = m[y0:y1, x0:x1].any(0)
    out, s = [], None
    for i, v in enumerate(r):
        if v and s is None:
            s = i
        elif not v and s is not None:
            if i - s >= minw:
                out.append((x0 + s, x0 + i - 1))
            s = None
    if s is not None:
        out.append((x0 + s, x1 - 1))
    return out


def centers(rs):
    return [round((a + b) / 2, 1) for a, b in rs]


def vertical_lines(m, y0: int, y1: int, minfrac: float = 0.85):
    """找竖直小节线：某列在该行区间内几乎全黑。"""
    sub = m[y0:y1, :]
    cnt = sub.sum(0)
    need = (y1 - y0) * minfrac
    xs = [x for x in range(1080) if cnt[x] >= need]
    # 合并相邻
    out, s, p = [], None, None
    for x in xs:
        if s is None:
            s = p = x
        elif x - p <= 2:
            p = x
        else:
            out.append(round((s + p) / 2, 1))
            s = p = x
    if s is not None:
        out.append(round((s + p) / 2, 1))
    return out


def report(path: str):
    a, b = load(REF), load(path)
    ma, mb = ink(a), ink(b)
    print(f"参考: {REF}\n渲染: {path}\n")
    print(f"{'项目':<28}{'参考图':<34}{'渲染图'}")
    print("-" * 96)

    def row(name, va, vb):
        print(f"{name:<28}{str(va):<34}{str(vb)}")

    # 1. 标题
    ta = xruns(ma, 40, 100, 300, 800)
    tb = xruns(mb, 40, 100, 300, 800)
    row("标题横向范围", (ta[0][0], ta[-1][1]) if ta else None,
        (tb[0][0], tb[-1][1]) if tb else None)
    ya = np.where(ma[30:110, 300:800].any(1))[0]
    yb = np.where(mb[30:110, 300:800].any(1))[0]
    row("标题纵向范围", (30 + int(ya.min()), 30 + int(ya.max())),
        (30 + int(yb.min()), 30 + int(yb.max())))

    # 2. 1=C
    ka = xruns(ma, 160, 195, 90, 260)
    kb = xruns(mb, 160, 195, 90, 260)
    row("1=C 横向范围", (ka[0][0], ka[-1][1]) if ka else None,
        (kb[0][0], kb[-1][1]) if kb else None)

    # 3. 小节线（系统 1）
    row("系统1 小节线 x", vertical_lines(ma, 250, 390), vertical_lines(mb, 250, 390))
    row("系统2 小节线 x", vertical_lines(ma, 545, 680), vertical_lines(mb, 545, 680))
    row("系统4 小节线 x", vertical_lines(ma, 1130, 1265), vertical_lines(mb, 1130, 1265))
    row("系统1 行首线 y", col_runs(ma, 55, 200, 500)[:1], col_runs(mb, 55, 200, 500)[:1])
    row("系统1 首小节线 y", col_runs(ma, 400, 200, 500), col_runs(mb, 400, 200, 500))

    # 4. 主旋律数字行（基线、x 中心）
    for i, (y0, y1) in enumerate([(250, 284), (541, 575), (839, 873), (1128, 1162)]):
        ra, rb = xruns(ma, y0, y1, 70, 1030, 2), xruns(mb, y0, y1, 70, 1030, 2)
        row(f"系统{i + 1} 旋律数字 x", centers(ra)[:10], centers(rb)[:10])

    # 5. 减时线 y
    row("系统1 旋律减时线 y", [y for y in range(280, 296)
                              if ma[y, 120:340].sum() > 100],
        [y for y in range(280, 296) if mb[y, 120:340].sum() > 100])
    row("系统1 低音减时线 y", [y for y in range(385, 400)
                              if ma[y, 120:230].sum() > 60],
        [y for y in range(385, 400) if mb[y, 120:230].sum() > 60])
    row("系统1 低音数字基线 y", [y for y in range(355, 390)
                                if ma[y, 120:370].sum() > 60],
        [y for y in range(355, 390) if mb[y, 120:370].sum() > 60])

    # 6. 指法圆圈（灰色，放宽阈值）
    ga, gb = a < 235, b < 235
    row("系统1 旋律圈 y 范围",
        [y for y in range(200, 250) if ga[y, 105:345].any()][:1] +
        [y for y in range(200, 250) if ga[y, 105:345].any()][-1:],
        [y for y in range(200, 250) if gb[y, 105:345].any()][:1] +
        [y for y in range(200, 250) if gb[y, 105:345].any()][-1:])

    # 7. 八度点
    row("系统1 旋律八度点 y", [y for y in range(238, 256)
                              if ma[y, 185:340].any()],
        [y for y in range(238, 256) if mb[y, 185:340].any()])
    row("系统1 低音八度点 y", [y for y in range(396, 416)
                              if ma[y, 115:375].any()],
        [y for y in range(396, 416) if mb[y, 115:375].any()])

    # 8. 页码
    pa = xruns(ma, 1350, 1390, 400, 700)
    pb = xruns(mb, 1350, 1390, 400, 700)
    row("页码 x 范围", (pa[0][0], pa[-1][1]) if pa else None,
        (pb[0][0], pb[-1][1]) if pb else None)

    # 9. 小节号
    ba = xruns(ma, 500, 528, 20, 90)
    bb = xruns(mb, 500, 528, 20, 90)
    row("〔4〕x 范围", (ba[0][0], ba[-1][1]) if ba else None,
        (bb[0][0], bb[-1][1]) if bb else None)

    # 10. 总体墨量
    row("墨量占比", round(ma.mean(), 4), round(mb.mean(), 4))


if __name__ == "__main__":
    report(sys.argv[1] if len(sys.argv) > 1 else "out/golden_p1.png")
