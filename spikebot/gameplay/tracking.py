"""Tiny helpers for estimating 'how long until X reaches Y' from noisy detections."""
from __future__ import annotations

from collections import deque


class ClosingTracker:
    """Tracks a signed gap d(t) (e.g. ring_x - shadow_x) and estimates time-to-zero."""

    def __init__(self, window_s: float = 0.3, max_jump: float = 250.0):
        self.window_s = window_s
        self.max_jump = max_jump
        self.samples: deque[tuple[float, float]] = deque()

    def clear(self) -> None:
        self.samples.clear()

    def update(self, t: float, d: float) -> None:
        if self.samples and abs(d - self.samples[-1][1]) > self.max_jump:
            self.samples.clear()  # detection jumped to another object
        self.samples.append((t, d))
        while self.samples and t - self.samples[0][0] > self.window_s:
            self.samples.popleft()

    def slope(self) -> float | None:
        n = len(self.samples)
        if n < 3:
            return None
        ts = [s[0] for s in self.samples]
        ds = [s[1] for s in self.samples]
        mt = sum(ts) / n
        md = sum(ds) / n
        var = sum((t - mt) ** 2 for t in ts)
        if var < 1e-9:
            return None
        return sum((t - mt) * (d - md) for t, d in zip(ts, ds)) / var

    def time_to_zero(self) -> float | None:
        """Seconds until the gap closes, or None if it isn't closing / unknown."""
        k = self.slope()
        if k is None:
            return None
        d = self.samples[-1][1]
        if d == 0:
            return 0.0
        if (d > 0 and k >= -1e-6) or (d < 0 and k <= 1e-6):
            return None
        return max(0.0, -d / k)

    @property
    def last(self) -> float | None:
        return self.samples[-1][1] if self.samples else None
