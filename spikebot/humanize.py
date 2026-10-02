"""Randomisation helpers that make input look less mechanical."""
from __future__ import annotations

import math
import random


def human_delay(rng: random.Random, lo: float, hi: float) -> float:
    """Random delay in [lo, hi], skewed toward the lower-middle like human reaction times."""
    if hi <= lo:
        return max(0.0, lo)
    mode = lo + (hi - lo) * 0.35
    return max(0.0, rng.triangular(lo, hi, mode))


def jitter_point(rng: random.Random, cx: float, cy: float, w: float, h: float,
                 frac: float) -> tuple[int, int]:
    """Gaussian point around the centre of a w x h box, clamped inside ``frac`` of it."""
    if frac <= 0:
        return round(cx), round(cy)
    max_dx = w / 2 * frac
    max_dy = h / 2 * frac
    dx = max(-max_dx, min(max_dx, rng.gauss(0, max_dx / 2)))
    dy = max(-max_dy, min(max_dy, rng.gauss(0, max_dy / 2)))
    return round(cx + dx), round(cy + dy)


def _ease(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * t)


def mouse_path(rng: random.Random, start: tuple[int, int], end: tuple[int, int],
               curved: bool = True, step_px: float = 25.0) -> list[tuple[int, int]]:
    """Points from start to end (end included) along an eased, slightly curved path."""
    (x0, y0), (x1, y1) = start, end
    dist = math.hypot(x1 - x0, y1 - y0)
    if dist < 2:
        return [end]
    steps = max(3, min(60, int(dist / step_px)))
    if curved:
        # Control point offset perpendicular to the straight line.
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        nx, ny = -(y1 - y0) / dist, (x1 - x0) / dist
        off = rng.uniform(-0.18, 0.18) * dist
        cx, cy = mx + nx * off, my + ny * off
    else:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    pts = []
    for i in range(1, steps + 1):
        t = _ease(i / steps)
        bx = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t ** 2 * x1
        by = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t ** 2 * y1
        pts.append((round(bx), round(by)))
    pts[-1] = (round(x1), round(y1))
    return pts
