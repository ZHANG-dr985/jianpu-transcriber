"""指法分配（右手旋律 DP，左手固定音型模板）+ 88 键键盘位置。"""
from __future__ import annotations

from typing import List, Optional, Sequence

from .model import PIANO_HIGH, PIANO_LOW, midi_to_name

# 手指相对拇指的半音偏移（右手：拇指最低）
RH_OFFSET = {1: 0, 2: 2, 3: 4, 4: 5, 5: 7}
# 左手：拇指最高
LH_OFFSET = {1: 0, 2: -2, 3: -4, 4: -5, 5: -7}

BLACK_KEYS = {1, 3, 5, 6, 8, 10}


def rh_fingering(midis: Sequence[int], start_center: Optional[int] = None) -> List[int]:
    """对一串右手单音分配 1..5 指（动态规划）。"""
    notes = [m for m in midis if m is not None]
    n = len(notes)
    if n == 0:
        return []
    fingers = [1, 2, 3, 4, 5]
    INF = 1e12
    # dp[f] = 最小代价（f 为当前音符所用手指）
    dp = {}
    back: List[dict] = []
    for f in fingers:
        hp = notes[0] - RH_OFFSET[f]
        center = start_center if start_center is not None else notes[0]
        c = 0.25 * abs(hp - center)
        if notes[0] % 12 in BLACK_KEYS and f in (1, 5):
            c += 0.4
        dp[f] = c
    back.append({})
    for i in range(1, n):
        ndp = {}
        nb = {}
        for f in fingers:
            hp = notes[i] - RH_OFFSET[f]
            best, bestg = INF, None
            for g, cprev in dp.items():
                hpg = notes[i - 1] - RH_OFFSET[g]
                move = abs(hp - hpg)
                c = cprev + move * 0.95
                if move > 7:
                    c += 2.2 * (move - 7)
                if f == g:
                    c += 1.1
                if f < g and move > 2:      # 跨指
                    c += 0.8
                if f > g and move > 4:
                    c += 0.8
                if notes[i] % 12 in BLACK_KEYS and f in (1, 5):
                    c += 0.35
                if c < best:
                    best, bestg = c, g
            ndp[f] = best
            nb[f] = bestg
        dp = ndp
        back.append(nb)
    # 回溯
    f = min(dp, key=lambda k: dp[k])
    out = [f]
    for i in range(n - 1, 0, -1):
        f = back[i][f]
        out.append(f)
    return list(reversed(out))


# 左手分解和弦音型：根 五 根 三 | 五 三 根 五（十六分音符）
LH_FIGURE = [0, 4, 0, 2, 4, 2, 0, 4]
LH_FINGER_TEMPLATE = [5, 2, 1, 2, 1, 2, 1, 2]


def lh_pattern(beats: int, grid: int = 4) -> List[tuple]:
    """返回 [(拍内位置, 音型序号, 指法)]，共 beats*grid 个十六分音符。"""
    out = []
    beats = max(1, beats)
    for b in range(beats):
        for k in range(grid):
            idx = b * grid + k
            fig = LH_FIGURE[idx % len(LH_FIGURE)]
            fing = LH_FINGER_TEMPLATE[idx % len(LH_FINGER_TEMPLATE)]
            out.append((b + k / grid, fig, fing))
    return out


def lh_fingering_for_pattern(count: int) -> List[int]:
    return [LH_FINGER_TEMPLATE[i % len(LH_FINGER_TEMPLATE)] for i in range(count)]


def key_position(midi: int) -> str:
    """88 键上的位置说明。"""
    if midi is None:
        return "-"
    if midi < PIANO_LOW or midi > PIANO_HIGH:
        return f"{midi_to_name(midi)}（超出 88 键）"
    idx = midi - PIANO_LOW + 1
    return f"{midi_to_name(midi)}（第 {idx} 键）"
