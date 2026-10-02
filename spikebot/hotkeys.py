"""Global hotkeys (work while the game has focus) via pynput."""
from __future__ import annotations

import logging
from typing import Callable

from .keys import normalize

log = logging.getLogger(__name__)

_PYNPUT_NAMES = {"escape": "esc", "return": "enter", "ctrl_l": "lctrl", "ctrl_r": "rctrl",
                 "shift_l": "lshift", "shift_r": "rshift", "alt_l": "lalt", "alt_r": "ralt",
                 "alt_gr": "ralt", "page_up": "pageup", "page_down": "pagedown"}


def pynput_key_name(key) -> str:
    """Normalise a pynput key object to our key names."""
    name = getattr(key, "name", None)
    if name:
        return normalize(_PYNPUT_NAMES.get(name, name))
    char = getattr(key, "char", None)
    if char:
        return normalize(char)
    vk = getattr(key, "vk", None)
    if vk is not None and 0x70 <= vk <= 0x87:
        return f"f{vk - 0x70 + 1}"
    return ""


class HotkeyManager:
    """Calls a handler when one of the configured keys is pressed (no auto-repeat)."""

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[], None]] = {}
        self._listener = None
        self._down: set[str] = set()
        self.available = False

    def bind(self, key_name: str, handler: Callable[[], None]) -> None:
        if key_name:
            self._handlers[normalize(key_name)] = handler

    def clear(self) -> None:
        self._handlers.clear()

    def _on_press(self, key) -> None:
        name = pynput_key_name(key)
        if not name or name in self._down:
            return
        self._down.add(name)
        handler = self._handlers.get(name)
        if handler is not None:
            try:
                handler()
            except Exception:
                log.exception("Hotkey handler for %s failed", name)

    def _on_release(self, key) -> None:
        self._down.discard(pynput_key_name(key))

    def start(self) -> bool:
        if self._listener is not None:
            return self.available
        try:
            from pynput import keyboard

            self._listener = keyboard.Listener(on_press=self._on_press,
                                               on_release=self._on_release)
            self._listener.daemon = True
            self._listener.start()
            self.available = True
        except Exception as exc:
            log.warning("Global hotkeys unavailable (%s). Use the UI Stop button.", exc)
            self._listener = None
            self.available = False
        return self.available

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self.available = False
