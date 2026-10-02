"""Shared stop / pause signalling used by every long-running loop."""
from __future__ import annotations

import threading
import time


class StopRequested(Exception):
    """Raised inside bot code when a stop was requested (F8, UI button, limits)."""


class BotControl:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._paused = threading.Event()
        self.stop_reason = ""
        self._on_pause_change: list = []

    # ----------------------------------------------------------------- stop
    def request_stop(self, reason: str = "stop requested") -> None:
        if not self._stop.is_set():
            self.stop_reason = reason
        self._stop.set()
        self._paused.clear()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def reset(self) -> None:
        self._stop.clear()
        self._paused.clear()
        self.stop_reason = ""

    # ---------------------------------------------------------------- pause
    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def set_paused(self, value: bool) -> None:
        if value:
            self._paused.set()
        else:
            self._paused.clear()
        for cb in list(self._on_pause_change):
            cb(value)

    def toggle_pause(self) -> bool:
        self.set_paused(not self.paused)
        return self.paused

    def on_pause_change(self, cb) -> None:
        self._on_pause_change.append(cb)

    # ------------------------------------------------------------- helpers
    def check(self) -> None:
        """Raise if stopped; block while paused."""
        if self._stop.is_set():
            raise StopRequested(self.stop_reason)
        while self._paused.is_set():
            if self._stop.wait(0.1):
                raise StopRequested(self.stop_reason)

    def sleep(self, seconds: float) -> None:
        """Interruptible sleep that also honours pause."""
        self.check()
        end = time.monotonic() + max(0.0, seconds)
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            if self._stop.wait(min(left, 0.05)):
                raise StopRequested(self.stop_reason)
            if self._paused.is_set():
                self.check()
        self.check()
