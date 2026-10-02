"""Screen capture of the game window's client area (mss)."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

from .config import WindowConfig
from .platform import IS_WINDOWS
from .vision import Frame

log = logging.getLogger(__name__)


class GameNotFound(RuntimeError):
    pass


class GameScreen:
    """Finds the game window and grabs its client area.

    mss handles are thread-bound on Windows, so create and use a GameScreen
    from the same thread.
    """

    RELOCATE_EVERY_S = 1.0

    def __init__(self, cfg: WindowConfig):
        self.cfg = cfg
        self._sct = None
        self.hwnd: int = 0
        self.title = ""
        self.rect: tuple[int, int, int, int] | None = None  # left, top, w, h
        self._last_locate = 0.0

    def _mss(self):
        if self._sct is None:
            import mss

            factory = getattr(mss, "MSS", None) or mss.mss  # mss 10 renamed the factory
            self._sct = factory()
        return self._sct

    def _monitor_rect(self) -> tuple[int, int, int, int]:
        mons = self._mss().monitors
        idx = self.cfg.fallback_monitor if 0 < self.cfg.fallback_monitor < len(mons) else 1
        m = mons[min(idx, len(mons) - 1)]
        return m["left"], m["top"], m["width"], m["height"]

    def locate(self, force: bool = False) -> tuple[int, int, int, int]:
        """Update and return the capture rectangle; raises GameNotFound."""
        now = time.monotonic()
        if not force and self.rect and now - self._last_locate < self.RELOCATE_EVERY_S:
            return self.rect
        self._last_locate = now
        if IS_WINDOWS:
            from . import win32

            if self.hwnd and win32.window_exists(self.hwnd):
                try:
                    self.rect = win32.client_rect(self.hwnd)
                    if self.rect[2] > 0 and self.rect[3] > 0:
                        return self.rect
                except OSError:
                    pass
            info = win32.find_window(self.cfg.title_contains, self.cfg.process_name)
            if info is not None:
                if info.hwnd != self.hwnd:
                    log.info("Game window: '%s' (%s) %dx%d at %d,%d", info.title,
                             info.process or "?", info.width, info.height, info.left, info.top)
                self.hwnd, self.title = info.hwnd, info.title
                self.rect = info.rect
                return self.rect
            self.hwnd = 0
            if not self.cfg.allow_monitor_fallback:
                raise GameNotFound(
                    f"No window whose title contains '{self.cfg.title_contains}'"
                    + (f" and process is '{self.cfg.process_name}'" if self.cfg.process_name else ""))
        self.rect = self._monitor_rect()
        self.title = "monitor"
        return self.rect

    def grab(self) -> Frame:
        left, top, w, h = self.locate()
        shot = self._mss().grab({"left": left, "top": top, "width": w, "height": h})
        img = np.asarray(shot)[:, :, :3]
        return Frame(img, time.monotonic(), origin=(left, top))

    def is_foreground(self) -> bool:
        if not IS_WINDOWS or not self.hwnd:
            return not IS_WINDOWS
        from . import win32

        return win32.foreground_window() == self.hwnd

    def focus(self) -> bool:
        if not IS_WINDOWS or not self.hwnd:
            return False
        from . import win32

        return win32.focus_window(self.hwnd)

    def close(self) -> None:
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None


class ImageSource:
    """Feeds pre-recorded images instead of the live screen (tests, replays, dry runs)."""

    def __init__(self, images: Iterable[np.ndarray | str | Path], loop: bool = False):
        self._images = list(images)
        self._i = 0
        self.loop = loop
        self.rect = (0, 0, 0, 0)
        self.hwnd = 0
        self.title = "images"

    def locate(self, force: bool = False):
        return self.rect

    def _load(self, item) -> np.ndarray:
        if isinstance(item, np.ndarray):
            return item
        img = cv2.imread(str(item), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(item)
        return img

    def grab(self) -> Frame:
        if self._i >= len(self._images):
            if not self.loop or not self._images:
                raise StopIteration("image source exhausted")
            self._i = 0
        img = self._load(self._images[self._i])
        self._i += 1
        self.rect = (0, 0, img.shape[1], img.shape[0])
        return Frame(img, time.monotonic())

    def is_foreground(self) -> bool:
        return True

    def focus(self) -> bool:
        return True

    def close(self) -> None:
        pass
