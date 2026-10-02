"""Records match frames + detections (+ your game-key presses) for tuning.

Output: ``recordings/<timestamp>/`` with ``00000.jpg``... and ``events.jsonl``.
Only the game keys from the config are logged (no other typing is recorded).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from ..hotkeys import pynput_key_name
from ..keys import normalize

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, root: str | Path, every_n: int = 3, max_frames: int = 3000,
                 width: int = 960, watch_keys: set[str] | None = None):
        self.dir = Path(root) / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.every_n = max(1, every_n)
        self.max_frames = max_frames
        self.width = width
        self.count = 0
        self._i = 0
        self._fh = (self.dir / "events.jsonl").open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self._listener = None
        self.watch_keys = {normalize(k) for k in (watch_keys or set()) if k}
        if self.watch_keys:
            self._start_key_log()
        log.info("Recording match frames to %s", self.dir)

    def _write(self, obj: dict) -> None:
        with self._lock:
            self._fh.write(json.dumps(obj) + "\n")

    def _start_key_log(self) -> None:
        try:
            from pynput import keyboard
        except Exception:
            return

        def on(kind):
            def cb(key):
                name = pynput_key_name(key)
                if name in self.watch_keys:
                    self._write({"t": time.monotonic(), "user_key": name, "kind": kind})
            return cb

        try:
            self._listener = keyboard.Listener(on_press=on("down"), on_release=on("up"))
            self._listener.daemon = True
            self._listener.start()
        except Exception as exc:
            log.debug("Key logging unavailable: %s", exc)

    def add(self, image: np.ndarray, info: dict) -> None:
        self._i += 1
        if self.count >= self.max_frames or (self._i - 1) % self.every_n:
            if info.get("actions"):
                self._write(dict(info, frame=None))
            return
        h, w = image.shape[:2]
        if w != self.width:
            image = cv2.resize(image, (self.width, round(h * self.width / w)),
                               interpolation=cv2.INTER_AREA)
        name = f"{self.count:05d}.jpg"
        cv2.imwrite(str(self.dir / name), image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        self.count += 1
        self._write(dict(info, frame=name))

    def close(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
        with self._lock:
            self._fh.close()
        log.info("Saved %d frames to %s", self.count, self.dir)
