"""Debug drawing of what the agent sees (live view and recordings)."""
from __future__ import annotations

import cv2
import numpy as np

from .perception import REF_W, Detections

PHASE_COLORS = {"defense": (80, 200, 80), "attack": (40, 140, 255), "air": (40, 40, 255),
                "unknown": (180, 180, 180)}


def annotate(image: np.ndarray, det: Detections, lines: list[str] | None = None,
             width: int = 960) -> np.ndarray:
    """Return a resized copy of ``image`` with detections drawn on it."""
    h, w = image.shape[:2]
    out = cv2.resize(image, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
    k = width / REF_W  # reference px -> output px

    def P(x, y):
        return int(round(x * k)), int(round(y * k))

    if det.net_x is not None:
        x = int(det.net_x * k)
        cv2.line(out, (x, 0), (x, out.shape[0]), (255, 120, 0), 1)
    if det.player is not None:
        cv2.circle(out, P(*det.player), 14, (255, 0, 255), 2)
        cv2.putText(out, "ME", P(det.player[0] - 14, det.player[1] - 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 2)
    for ring, color, label in ((det.floor_ring, (0, 255, 0), "LAND"),
                               (det.air_ring, (0, 0, 255), "SPIKE")):
        if ring is None:
            continue
        cv2.ellipse(out, (P(ring.x, ring.y), (int(ring.w * k), int(ring.h * k)), 0), color, 2)
        cv2.putText(out, label, P(ring.x + ring.w / 2 + 4, ring.y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, color, 1)
    if det.shadow is not None:
        cv2.drawMarker(out, P(det.shadow[0], det.shadow[1]), (0, 255, 255),
                       cv2.MARKER_TILTED_CROSS, 14, 2)
    color = PHASE_COLORS.get(det.phase, (255, 255, 255))
    text = [f"phase: {det.phase}"] + list(lines or [])
    y = 20
    for t in text:
        cv2.putText(out, t, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
        cv2.putText(out, t, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        y += 18
    return out
