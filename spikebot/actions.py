"""Humanised mouse/keyboard actions on top of a raw input backend."""
from __future__ import annotations

import logging
import random
import time

from .config import HumanizeConfig
from .control import BotControl
from .humanize import human_delay, jitter_point, mouse_path
from .vision import Frame, Match

log = logging.getLogger(__name__)


class DryRunBackend:
    """Records actions instead of sending them (tests / dry-run mode)."""

    def __init__(self) -> None:
        self.events: list[tuple] = []
        self._pos = (0, 0)

    def key_down(self, name: str) -> None:
        self.events.append(("key_down", name))

    def key_up(self, name: str) -> None:
        self.events.append(("key_up", name))

    def move_to(self, x: int, y: int) -> None:
        self._pos = (x, y)

    def mouse_down(self) -> None:
        self.events.append(("mouse_down", self._pos))

    def mouse_up(self) -> None:
        self.events.append(("mouse_up", self._pos))

    def cursor_pos(self) -> tuple[int, int]:
        return self._pos

    def clicks(self) -> list[tuple[int, int]]:
        return [e[1] for e in self.events if e[0] == "mouse_up"]

    def taps(self) -> list[str]:
        return [e[1] for e in self.events if e[0] == "key_up"]


def make_backend(dry_run: bool):
    from .platform import IS_WINDOWS

    if dry_run or not IS_WINDOWS:
        if not dry_run:
            log.warning("Not on Windows - input is simulated (dry run)")
        return DryRunBackend()
    from .win32 import SendInputBackend

    return SendInputBackend()


class InputController:
    """All input the bot sends goes through here.

    * keys that are held are tracked so ``release_all`` can always clean up
    * every sleep is interruptible by the emergency stop
    * ``guard`` is called before each action; it should raise if input is not
      allowed right now (e.g. the game window lost focus)
    """

    def __init__(self, backend, humanize: HumanizeConfig, control: BotControl,
                 rng: random.Random | None = None, guard=None):
        self.backend = backend
        self.h = humanize
        self.control = control
        self.rng = rng or random.Random()
        self.guard = guard or (lambda: None)
        self.held: set[str] = set()

    # ------------------------------------------------------------- timing
    def human_wait(self, lo: float | None = None, hi: float | None = None) -> float:
        d = human_delay(self.rng, self.h.delay_min_s if lo is None else lo,
                        self.h.delay_max_s if hi is None else hi)
        self.control.sleep(d)
        return d

    def _press_time(self) -> float:
        return human_delay(self.rng, self.h.press_min_s, self.h.press_max_s)

    # ----------------------------------------------------------- keyboard
    def key_down(self, name: str) -> None:
        if not name or name in self.held:
            return
        self.guard()
        self.backend.key_down(name)
        self.held.add(name)

    def key_up(self, name: str) -> None:
        if not name or name not in self.held:
            return
        self.backend.key_up(name)
        self.held.discard(name)

    def tap(self, name: str, hold_s: float | None = None) -> None:
        """Press and release a key. ``hold_s`` overrides the random press time."""
        if not name:
            return
        self.guard()
        self.backend.key_down(name)
        self.held.add(name)
        try:
            dur = self._press_time() if hold_s is None else hold_s
            if dur > 0:
                time.sleep(dur)  # short & must release even if a stop arrives
        finally:
            self.backend.key_up(name)
            self.held.discard(name)

    def release_all(self) -> None:
        for name in list(self.held):
            try:
                self.backend.key_up(name)
            except Exception as exc:
                log.warning("Could not release %s: %s", name, exc)
            self.held.discard(name)

    # -------------------------------------------------------------- mouse
    def move_to(self, x: int, y: int) -> None:
        self.guard()
        try:
            start = self.backend.cursor_pos()
        except Exception:
            start = (x, y)
        pts = mouse_path(self.rng, start, (x, y), curved=self.h.curved_moves)
        total = human_delay(self.rng, self.h.move_min_s, self.h.move_max_s)
        step = total / max(1, len(pts))
        for px, py in pts:
            self.control.check()
            self.backend.move_to(px, py)
            if step > 0:
                time.sleep(step)

    def click_at(self, x: int, y: int) -> None:
        self.move_to(x, y)
        self.control.sleep(human_delay(self.rng, 0.03, 0.12))
        self.guard()
        self.backend.mouse_down()
        try:
            time.sleep(self._press_time())
        finally:
            self.backend.mouse_up()

    def click_match(self, frame: Frame, m: Match) -> tuple[int, int]:
        """Click somewhere inside a matched template (screen coordinates)."""
        cx, cy = jitter_point(self.rng, m.x, m.y, m.w, m.h, self.h.click_jitter)
        sx, sy = frame.origin[0] + cx, frame.origin[1] + cy
        self.click_at(sx, sy)
        return sx, sy
