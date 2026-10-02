"""What the gameplay agent can see in a frame.

All returned coordinates are in *reference pixels*: the frame rescaled to a
width of 1920, so config values (tolerances, distances) work at any window size.

Cues used (all verified on real v8.0.109w screenshots):
* HUD action labels (bottom-left): "Jump" = defending, "Spike" = our attack,
  "Cancel" = we're in the air for a spike. Key glyphs differ per user, so only
  the label text is matched.
* Controlled player: light-blue downward triangle above the head.
* Landing spot: small white ring (ellipse) on the floor where the ball will land.
* Spike point: large white ring in the air (hitting the ball inside it gives a
  "White-Circle Hit").
* Net: tall saturated-blue post.
* Ball shadow: small dark ellipse on the floor under the ball.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..config import GameplayConfig
from ..vision import Frame, TemplateLibrary

REF_W = 1920.0

HUD_LABELS = {"jump": "hud_label_jump", "spike": "hud_label_spike", "cancel": "hud_label_cancel"}
HUD_MARGIN = 0.08


@dataclass
class Ring:
    x: float
    y: float
    w: float  # full axis lengths (reference px)
    h: float
    angle: float = 0.0
    score: float = 0.0


@dataclass
class Detections:
    t: float
    phase: str = "unknown"  # defense | attack | air | unknown
    hud: dict[str, float] = field(default_factory=dict)
    player: tuple[float, float] | None = None  # marker tip (x, y), reference px
    net_x: float | None = None
    floor_ring: Ring | None = None
    air_ring: Ring | None = None
    shadow: tuple[float, float, float] | None = None  # x, y, width
    ref_scale: float = 1.0  # frame px -> reference px

    def to_json(self) -> dict:
        def r(v):
            return None if v is None else [round(float(a), 1) for a in v]

        def ring(v):
            return None if v is None else [round(v.x, 1), round(v.y, 1), round(v.w, 1),
                                           round(v.h, 1), round(v.angle, 1), round(v.score, 2)]
        return {"t": round(self.t, 3), "phase": self.phase,
                "hud": {k: round(v, 2) for k, v in self.hud.items()},
                "player": r(self.player), "net_x": self.net_x,
                "floor_ring": ring(self.floor_ring), "air_ring": ring(self.air_ring),
                "shadow": r(self.shadow)}


def _white_mask(hsv: np.ndarray, s_max: int, v_min: int) -> np.ndarray:
    return cv2.inRange(hsv, (0, 0, v_min), (180, s_max, 255))


def ellipse_coverage(mask: np.ndarray, cx: float, cy: float, a: float, b: float,
                     angle_deg: float, scale: float = 1.0, n: int = 48) -> float:
    """Fraction of points on an ellipse (axes a, b = half-lengths) that hit the mask."""
    h, w = mask.shape[:2]
    th = math.radians(angle_deg)
    ct, st = math.cos(th), math.sin(th)
    hits = 0
    total = 0
    for i in range(n):
        t = 2 * math.pi * i / n
        ex, ey = a * scale * math.cos(t), b * scale * math.sin(t)
        x = int(round(cx + ex * ct - ey * st))
        y = int(round(cy + ex * st + ey * ct))
        if 0 <= x < w and 0 <= y < h:
            total += 1
            hits += 1 if mask[y, x] else 0
    return hits / total if total else 0.0


class Perception:
    def __init__(self, cfg: GameplayConfig, library: TemplateLibrary | None = None):
        self.cfg = cfg
        self.library = library
        self._prev_player: tuple[float, float] | None = None
        self._prev_shadow: tuple[float, float, float] | None = None
        self._kernel3 = np.ones((3, 3), np.uint8)

    # ------------------------------------------------------------------ HUD
    def hud_phase(self, frame: Frame) -> tuple[str, dict[str, float]]:
        scores: dict[str, float] = {}
        if self.library is None:
            return "unknown", scores
        for key, name in HUD_LABELS.items():
            if self.library.has(name):
                m = self.library.best(frame, name)
                scores[key] = m.score if m else 0.0
        thr = {k: self.library.threshold_for(n) for k, n in HUD_LABELS.items()}
        ok = {k: v for k, v in scores.items() if v >= thr[k]}
        jump, spike = scores.get("jump", 0.0), scores.get("spike", 0.0)
        # "Jump" and "Spike" share the same HUD slot - require a clear winner.
        if "spike" in ok and spike - jump >= HUD_MARGIN:
            return ("air" if "cancel" in ok else "attack"), scores
        if "jump" in ok and jump - spike >= HUD_MARGIN:
            return "defense", scores
        return "unknown", scores

    # ---------------------------------------------------------------- frame
    def detect(self, frame: Frame) -> Detections:
        phase, hud = self.hud_phase(frame)
        aw = min(self.cfg.analysis_width, frame.width)
        a_scale = aw / frame.width
        img = frame.image if aw == frame.width else cv2.resize(
            frame.image, (aw, round(frame.height * a_scale)), interpolation=cv2.INTER_AREA)
        to_ref = REF_W / aw  # analysis px -> reference px
        s = aw / REF_W  # reference px -> analysis px
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        white = _white_mask(hsv, 70, 205)
        det = Detections(t=frame.timestamp, phase=phase, hud=hud, ref_scale=REF_W / frame.width)

        p = self._find_marker(hsv, s)
        if p is not None:
            det.player = (p[0] * to_ref, p[1] * to_ref)
            self._prev_player = det.player
        det.net_x = self._find_net(hsv, s)
        if det.net_x is not None:
            det.net_x *= to_ref
        fr = self._find_floor_ring(white, s)
        if fr is not None:
            det.floor_ring = Ring(fr.x * to_ref, fr.y * to_ref, fr.w * to_ref, fr.h * to_ref,
                                  fr.angle, fr.score)
        ar = self._find_air_ring(white, s)
        if ar is not None:
            det.air_ring = Ring(ar.x * to_ref, ar.y * to_ref, ar.w * to_ref, ar.h * to_ref,
                                ar.angle, ar.score)
        floor_y = None
        if det.floor_ring is not None:
            floor_y = det.floor_ring.y / to_ref
        sh = self._find_shadow(hsv, s, floor_y, white)
        if sh is not None:
            det.shadow = (sh[0] * to_ref, sh[1] * to_ref, sh[2] * to_ref)
            self._prev_shadow = det.shadow
        else:
            self._prev_shadow = None
        return det

    # ------------------------------------------------------------ detectors
    def _find_marker(self, hsv: np.ndarray, s: float) -> tuple[float, float] | None:
        H = hsv.shape[0]
        lo = tuple(int(v) for v in self.cfg.marker_hsv_low)
        hi = tuple(int(v) for v in self.cfg.marker_hsv_high)
        m = cv2.inRange(hsv, lo, hi)
        m[: int(H * 0.15)] = 0
        m[int(H * 0.88):] = 0
        n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
        cands = []
        for i in range(1, n):
            x, y, w, h, a = st[i]
            if not (max(4, 16 * s) <= w <= 48 * s + 2 and max(2, 8 * s) <= h <= 28 * s + 2):
                continue
            if not 1.3 <= w / max(h, 1) <= 3.4:
                continue
            fill = a / float(w * h)
            if not 0.33 <= fill <= 0.72:
                continue
            blob = lab[y:y + h, x:x + w] == i
            k = max(1, h // 3)
            if blob[:k].sum() <= blob[-k:].sum() * 1.4:  # must be wider at the top (pointing down)
                continue
            cands.append((x + w / 2.0, float(y + h), fill))
        if not cands:
            return None
        if self._prev_player is not None:
            px, py = self._prev_player[0] * s, self._prev_player[1] * s
            return min(cands, key=lambda c: (c[0] - px) ** 2 + (c[1] - py) ** 2)[:2]
        return min(cands, key=lambda c: abs(c[2] - 0.55))[:2]

    def _find_net(self, hsv: np.ndarray, s: float) -> float | None:
        H = hsv.shape[0]
        y0, y1 = int(H * 0.55), int(H * 0.9)
        m = cv2.inRange(hsv[y0:y1], (98, 150, 90), (118, 255, 255))
        col = m.sum(axis=0).astype(np.float32) / 255.0
        k = max(3, int(24 * s))
        sm = np.convolve(col, np.ones(k, np.float32) / k, mode="same")
        x = int(np.argmax(sm))
        if sm[x] < 0.3 * (y1 - y0):
            return None
        return float(x)

    def _ring_candidates(self, mask: np.ndarray, hollow_max: float, coverage_min: float):
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        for c in cnts:
            if len(c) < 10:
                continue
            x, y, w, h = cv2.boundingRect(c)
            fill = cv2.countNonZero(mask[y:y + h, x:x + w]) / float(w * h)
            if fill > hollow_max:
                continue
            (ex, ey), (ea, eb), ang = cv2.fitEllipse(c)
            if ea < 2 or eb < 2:
                continue
            cov = ellipse_coverage(mask, ex, ey, ea / 2, eb / 2, ang)
            if cov < coverage_min:
                continue
            inner = ellipse_coverage(mask, ex, ey, ea / 2, eb / 2, ang, scale=0.45)
            if inner > 0.3:
                continue
            yield (x, y, w, h), (ex, ey, ea, eb, ang), cov - inner

    def _find_floor_ring(self, white: np.ndarray, s: float) -> Ring | None:
        H, W = white.shape[:2]
        m = white.copy()
        m[: int(H * 0.5)] = 0
        m[:, : int(W * 0.13)] = 0  # HUD buttons
        best = None
        for (x, y, w, h), (ex, ey, ea, eb, ang), score in self._ring_candidates(m, 0.6, 0.6):
            if not (25 * s - 2 <= w <= 110 * s + 2 and 8 * s - 2 <= h <= 45 * s + 2):
                continue
            if not 1.8 <= w / max(h, 1) <= 4.8:
                continue
            if best is None or score > best.score:
                best = Ring(x + w / 2.0, y + h / 2.0, float(w), float(h), ang, score)
        return best

    def _find_air_ring(self, white: np.ndarray, s: float) -> Ring | None:
        H, W = white.shape[:2]
        m = white.copy()
        m[int(H * 0.74):] = 0
        m[: int(H * 0.12)] = 0
        m[:, : int(W * 0.1)] = 0
        m[:, int(W * 0.9):] = 0
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, self._kernel3)
        best = None
        for (x, y, w, h), (ex, ey, ea, eb, ang), score in self._ring_candidates(m, 0.3, 0.55):
            major = max(w, h)
            if not (60 * s <= major <= 360 * s and min(w, h) >= 20 * s):
                continue
            if best is None or score > best.score:
                best = Ring(float(ex), float(ey), float(w), float(h), ang, score)
        return best

    def _find_shadow(self, hsv: np.ndarray, s: float, floor_y: float | None,
                     white: np.ndarray) -> tuple[float, float, float] | None:
        """Small dark ellipse on the floor band (the ball's shadow)."""
        H, W = hsv.shape[:2]
        if floor_y is None:
            if self._prev_shadow is None:
                return None
            floor_y = self._prev_shadow[1] * s
        half = int(45 * s) + 2
        y0, y1 = max(0, int(floor_y) - half), min(H, int(floor_y) + half)
        if y1 - y0 < 6:
            return None
        v = hsv[y0:y1, :, 2]
        k = int(61 * s) | 1
        bg = cv2.medianBlur(v, max(3, min(k, 255)))
        dark = ((bg.astype(np.int16) - v.astype(np.int16)) > 35).astype(np.uint8) * 255
        dark[:, : int(W * 0.13)] = 0
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, self._kernel3)
        n, _, st, cen = cv2.connectedComponentsWithStats(dark, 8)
        # "Disturbance" = anything that isn't plain floor. Player shadows have
        # legs/shoes right above them; the ball's shadow sits on empty floor.
        full_v = hsv[:, :, 2].astype(np.int16)
        cands = []
        for i in range(1, n):
            x, y, w, h, a = st[i]
            if not (10 * s <= w <= 48 * s + 2 and 3 * s <= h <= 18 * s + 2):
                continue
            if not 1.3 <= w / max(h, 1) <= 6.0:
                continue
            if a / float(w * h) < 0.45:
                continue
            ay1 = y0 + y
            ay0 = max(0, ay1 - int(3 * h) - 2)
            above = full_v[ay0:ay1, x:x + w]
            if above.size == 0:
                continue
            ref = np.median(full_v[ay0:ay1 + h, max(0, x - w):x + 2 * w])
            disturb = float(np.mean(np.abs(above - ref) > 30))
            if disturb > 0.2:
                continue
            cands.append((cen[i][0], y0 + cen[i][1], float(w), disturb))
        if not cands:
            return None
        if self._prev_shadow is not None:
            px = self._prev_shadow[0] * s
            best = min(cands, key=lambda c: abs(c[0] - px))
        else:
            best = min(cands, key=lambda c: c[3])
        return best[0], best[1], best[2]
