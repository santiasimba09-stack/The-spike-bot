"""Key names used in the config, mapped to Windows virtual-key codes.

Names are case-insensitive: letters (``a``), digits (``1``), ``f1``-``f24``,
``space``, ``enter``, ``esc``, ``tab``, ``backspace``, arrows (``left``,
``right``, ``up``, ``down``), ``shift``/``ctrl``/``alt`` (and ``lshift``,
``rctrl``...), ``insert``, ``delete``, ``home``, ``end``, ``pageup``,
``pagedown``, ``num0``-``num9`` and common punctuation.
"""
from __future__ import annotations

ALIASES = {
    "escape": "esc",
    "return": "enter",
    "spacebar": "space",
    " ": "space",
    "control": "ctrl",
    "arrowleft": "left",
    "arrowright": "right",
    "arrowup": "up",
    "arrowdown": "down",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "del": "delete",
    "ins": "insert",
    "lcontrol": "lctrl",
    "rcontrol": "rctrl",
}

_VK: dict[str, int] = {
    "space": 0x20,
    "enter": 0x0D,
    "esc": 0x1B,
    "tab": 0x09,
    "backspace": 0x08,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "lshift": 0xA0,
    "rshift": 0xA1,
    "lctrl": 0xA2,
    "rctrl": 0xA3,
    "lalt": 0xA4,
    "ralt": 0xA5,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
    ";": 0xBA,
    "'": 0xDE,
    "[": 0xDB,
    "]": 0xDD,
    "-": 0xBD,
    "=": 0xBB,
    "`": 0xC0,
    "\\": 0xDC,
}
for _c in "abcdefghijklmnopqrstuvwxyz0123456789":
    _VK[_c] = ord(_c.upper())
for _n in range(1, 25):
    _VK[f"f{_n}"] = 0x70 + _n - 1
for _n in range(10):
    _VK[f"num{_n}"] = 0x60 + _n

# Keys that need KEYEVENTF_EXTENDEDKEY when injected by scan code.
EXTENDED = {"left", "right", "up", "down", "insert", "delete", "home", "end",
            "pageup", "pagedown", "rctrl", "ralt"}

# Set-1 scan codes, used if MapVirtualKey is unavailable or returns 0.
_SCAN_FALLBACK = {
    "esc": 0x01, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06, "6": 0x07,
    "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B, "-": 0x0C, "=": 0x0D,
    "backspace": 0x0E, "tab": 0x0F, "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13,
    "t": 0x14, "y": 0x15, "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19, "[": 0x1A,
    "]": 0x1B, "enter": 0x1C, "ctrl": 0x1D, "lctrl": 0x1D, "rctrl": 0x1D,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23, "j": 0x24,
    "k": 0x25, "l": 0x26, ";": 0x27, "'": 0x28, "`": 0x29, "shift": 0x2A,
    "lshift": 0x2A, "\\": 0x2B, "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F,
    "b": 0x30, "n": 0x31, "m": 0x32, ",": 0x33, ".": 0x34, "/": 0x35,
    "rshift": 0x36, "alt": 0x38, "lalt": 0x38, "ralt": 0x38, "space": 0x39,
    "f1": 0x3B, "f2": 0x3C, "f3": 0x3D, "f4": 0x3E, "f5": 0x3F, "f6": 0x40,
    "f7": 0x41, "f8": 0x42, "f9": 0x43, "f10": 0x44, "f11": 0x57, "f12": 0x58,
    "home": 0x47, "up": 0x48, "pageup": 0x49, "left": 0x4B, "right": 0x4D,
    "end": 0x4F, "down": 0x50, "pagedown": 0x51, "insert": 0x52, "delete": 0x53,
    "num7": 0x47, "num8": 0x48, "num9": 0x49, "num4": 0x4B, "num5": 0x4C,
    "num6": 0x4D, "num1": 0x4F, "num2": 0x50, "num3": 0x51, "num0": 0x52,
}


def normalize(name: str) -> str:
    if name == " ":
        return "space"
    n = (name or "").strip().lower()
    return ALIASES.get(n, n)


def is_valid_key(name: str) -> bool:
    return normalize(name) in _VK


def vk_code(name: str) -> int:
    n = normalize(name)
    if n not in _VK:
        raise KeyError(f"Unknown key name: {name!r}")
    return _VK[n]


def fallback_scan_code(name: str) -> int:
    return _SCAN_FALLBACK.get(normalize(name), 0)


def is_extended(name: str) -> bool:
    return normalize(name) in EXTENDED


def all_key_names() -> list[str]:
    return sorted(_VK)
