"""Template loading and OpenCV template matching.

Templates live in ``templates/`` and are described by ``templates/templates.yaml``::

    hud_pause:
      file: hud_pause.png
      source_width: 1920        # width of the game window the crop was taken from
      threshold: 0.80           # optional, falls back to matching.default_threshold
      mode: white               # color | gray | white  (white = bright, unsaturated text/icons)
      region: [0.9, 0.0, 1.0, 0.12]   # optional normalised search area x0, y0, x1, y1

Templates are rescaled to the live window size (``window_width / source_width``),
so a crop taken at 2560x1440 still matches a 1920x1080 window.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
import yaml

log = logging.getLogger(__name__)

MODES = ("color", "gray", "white")
MANIFEST = "templates.yaml"


@dataclass
class Match:
    name: str
    score: float
    x: int  # centre, frame coordinates
    y: int
    w: int
    h: int

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.x - self.w // 2, self.y - self.h // 2, self.w, self.h


@dataclass
class Template:
    name: str
    image: np.ndarray
    source_width: int = 1920
    threshold: float | None = None
    mode: str = "color"
    region: tuple[float, float, float, float] | None = None
    file: str = ""
    note: str = ""
    _cache: dict = field(default_factory=dict, repr=False)

    def prepared(self, scale: float) -> np.ndarray:
        key = round(scale, 4)
        if key not in self._cache:
            img = self.image
            if abs(scale - 1.0) > 1e-3:
                interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
                w = max(1, round(img.shape[1] * scale))
                h = max(1, round(img.shape[0] * scale))
                img = cv2.resize(img, (w, h), interpolation=interp)
            self._cache[key] = convert(img, self.mode)
        return self._cache[key]


def whiteness(bgr: np.ndarray) -> np.ndarray:
    """Soft mask of bright, unsaturated pixels (white text/icons) in [0, 1]."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    s = hsv[..., 1].astype(np.float32)
    v = hsv[..., 2].astype(np.float32)
    return np.clip((v - 150.0) / 105.0, 0, 1) * np.clip((90.0 - s) / 90.0, 0, 1)


def convert(bgr: np.ndarray, mode: str) -> np.ndarray:
    if mode == "gray":
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if mode == "white":
        # A light blur makes thin text robust to the resampling of scaled templates.
        return cv2.GaussianBlur(whiteness(bgr), (3, 3), 0)
    return bgr


class Frame:
    """A captured game frame with cached per-mode conversions."""

    def __init__(self, image: np.ndarray, timestamp: float = 0.0,
                 origin: tuple[int, int] = (0, 0)):
        if image.ndim == 3 and image.shape[2] == 4:
            image = image[:, :, :3]
        self.image = np.ascontiguousarray(image)
        self.timestamp = timestamp
        self.origin = origin
        """Screen position of the frame's top-left pixel (for clicking)."""
        self._conv: dict[tuple, np.ndarray] = {}

    @property
    def width(self) -> int:
        return self.image.shape[1]

    @property
    def height(self) -> int:
        return self.image.shape[0]

    def as_mode(self, mode: str, box: tuple[int, int, int, int] | None = None) -> np.ndarray:
        """Image converted for ``mode`` (cached), optionally only the box x0, y0, x1, y1."""
        key = (mode, box)
        if key not in self._conv:
            img = self.image if box is None else self.image[box[1]:box[3], box[0]:box[2]]
            self._conv[key] = convert(img, mode)
        return self._conv[key]


class TemplateLibrary:
    """Loads templates from a folder + manifest and matches them against frames."""

    def __init__(self, folder: str | Path, default_threshold: float = 0.82,
                 scales: Iterable[float] = (1.0,)):
        self.folder = Path(folder)
        self.default_threshold = default_threshold
        self.scales = list(scales) or [1.0]
        self.templates: dict[str, Template] = {}
        self.reload()

    # ---------------------------------------------------------------- loading
    def manifest_path(self) -> Path:
        return self.folder / MANIFEST

    def read_manifest(self) -> dict:
        p = self.manifest_path()
        if not p.exists():
            return {}
        with p.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data if isinstance(data, dict) else {}

    def reload(self) -> None:
        self.templates.clear()
        if not self.folder.exists():
            log.warning("Templates folder %s does not exist", self.folder)
            return
        manifest = self.read_manifest()
        names = set(manifest)
        # PNGs without a manifest entry are loaded with defaults.
        for png in self.folder.glob("*.png"):
            names.add(png.stem)
        for name in sorted(names):
            meta = manifest.get(name) or {}
            file = meta.get("file", f"{name}.png")
            path = self.folder / file
            if not path.exists():
                continue
            img = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if img is None:
                log.warning("Could not read template %s", path)
                continue
            mode = meta.get("mode", "color")
            if mode not in MODES:
                log.warning("Template %s: unknown mode %r, using color", name, mode)
                mode = "color"
            region = meta.get("region")
            if region is not None:
                try:
                    region = tuple(float(v) for v in region)
                    if len(region) != 4:
                        raise ValueError
                except (TypeError, ValueError):
                    log.warning("Template %s: bad region %r ignored", name, region)
                    region = None
            self.templates[name] = Template(
                name=name,
                image=img,
                source_width=int(meta.get("source_width", 1920)),
                threshold=meta.get("threshold"),
                mode=mode,
                region=region,
                file=file,
                note=str(meta.get("note", "")),
            )
        log.info("Loaded %d templates from %s", len(self.templates), self.folder)

    def save_template(self, name: str, image: np.ndarray, source_width: int,
                      mode: str = "color", threshold: float | None = None,
                      region: tuple[float, float, float, float] | None = None,
                      note: str = "") -> Path:
        """Write a PNG + manifest entry, then reload."""
        self.folder.mkdir(parents=True, exist_ok=True)
        file = f"{name}.png"
        path = self.folder / file
        if not cv2.imwrite(str(path), image):
            raise OSError(f"Could not write {path}")
        manifest = self.read_manifest()
        entry = dict(manifest.get(name) or {})
        entry.update({"file": file, "source_width": int(source_width), "mode": mode})
        # A fresh capture defines threshold/region (None = default / whole screen).
        entry.pop("threshold", None)
        entry.pop("region", None)
        if threshold is not None:
            entry["threshold"] = round(float(threshold), 3)
        if region is not None:
            entry["region"] = [round(float(v), 3) for v in region]
        if note:
            entry["note"] = note
        manifest[name] = entry
        self.write_manifest(manifest)
        self.reload()
        return path

    def write_manifest(self, manifest: dict) -> None:
        p = self.manifest_path()
        header = ("# Template manifest - see spikebot/vision.py for the format.\n"
                  "# Edit thresholds/regions here or from the UI's Templates tab.\n")
        with p.open("w", encoding="utf-8") as fh:
            fh.write(header)
            yaml.safe_dump(manifest, fh, sort_keys=True, allow_unicode=True, default_flow_style=None, width=120)

    def update_meta(self, name: str, **changes) -> None:
        manifest = self.read_manifest()
        entry = dict(manifest.get(name) or {"file": f"{name}.png"})
        for k, v in changes.items():
            if v is None:
                entry.pop(k, None)
            else:
                entry[k] = v
        manifest[name] = entry
        self.write_manifest(manifest)
        self.reload()

    # --------------------------------------------------------------- matching
    def has(self, name: str) -> bool:
        return name in self.templates

    def threshold_for(self, name: str) -> float:
        t = self.templates.get(name)
        if t is not None and t.threshold is not None:
            return float(t.threshold)
        return self.default_threshold

    def best(self, frame: Frame, name: str) -> Match | None:
        """Best match for a template regardless of threshold (None if not loaded)."""
        tpl = self.templates.get(name)
        if tpl is None:
            return None
        return match_template(frame, tpl, self.scales)

    def find(self, frame: Frame, name: str, threshold: float | None = None) -> Match | None:
        """Match above threshold, else None."""
        m = self.best(frame, name)
        if m is None:
            return None
        thr = self.threshold_for(name) if threshold is None else threshold
        return m if m.score >= thr else None

    def visible(self, frame: Frame, name: str) -> bool:
        return self.find(frame, name) is not None


def region_box(frame_w: int, frame_h: int,
               region: tuple[float, float, float, float] | None) -> tuple[int, int, int, int]:
    if not region:
        return 0, 0, frame_w, frame_h
    x0, y0, x1, y1 = region
    X0 = int(max(0.0, min(x0, x1)) * frame_w)
    Y0 = int(max(0.0, min(y0, y1)) * frame_h)
    X1 = int(min(1.0, max(x0, x1)) * frame_w)
    Y1 = int(min(1.0, max(y0, y1)) * frame_h)
    return X0, Y0, max(X1, X0 + 1), max(Y1, Y0 + 1)


_MIN_STD = {"color": 2.0, "gray": 2.0, "white": 0.03}


def _window_std(hay: np.ndarray, tw: int, th: int, out_shape: tuple[int, ...]) -> np.ndarray:
    """Std-dev of every template-sized window (aligned with matchTemplate output)."""
    h = hay.astype(np.float32)
    if h.ndim == 3:
        h = h.mean(axis=2)
    mean = cv2.blur(h, (tw, th), anchor=(0, 0), borderType=cv2.BORDER_CONSTANT)
    sq = cv2.blur(h * h, (tw, th), anchor=(0, 0), borderType=cv2.BORDER_CONSTANT)
    var = np.maximum(sq - mean * mean, 0)
    return np.sqrt(var[: out_shape[0], : out_shape[1]])


def match_template(frame: Frame, tpl: Template, scales: Iterable[float] = (1.0,)) -> Match | None:
    base = frame.width / float(tpl.source_width or frame.width)
    X0, Y0, X1, Y1 = region_box(frame.width, frame.height, tpl.region)
    best: Match | None = None
    for s in scales:
        t = tpl.prepared(base * s)
        th, tw = t.shape[:2]
        # Grow the region if it is smaller than the template.
        x0, y0, x1, y1 = X0, Y0, X1, Y1
        if x1 - x0 < tw:
            x0, x1 = max(0, x0 - (tw - (x1 - x0))), min(frame.width, x1 + tw)
        if y1 - y0 < th:
            y0, y1 = max(0, y0 - (th - (y1 - y0))), min(frame.height, y1 + th)
        if y1 - y0 < th or x1 - x0 < tw:
            continue
        hay = frame.as_mode(tpl.mode, (x0, y0, x1, y1))
        if hay.shape[0] < th or hay.shape[1] < tw:
            continue
        if float(np.std(t)) < 1e-3:
            continue  # flat template -> correlation undefined
        res = cv2.matchTemplate(hay, t, cv2.TM_CCOEFF_NORMED)
        res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
        # Normalised correlation is meaningless (OpenCV returns ~1) on flat
        # windows, e.g. an empty whiteness map - zero those out.
        res[_window_std(hay, tw, th, res.shape) < _MIN_STD[tpl.mode]] = 0.0
        _, val, _, loc = cv2.minMaxLoc(res)
        if best is None or val > best.score:
            best = Match(tpl.name, float(val), x0 + loc[0] + tw // 2, y0 + loc[1] + th // 2, tw, th)
    return best
