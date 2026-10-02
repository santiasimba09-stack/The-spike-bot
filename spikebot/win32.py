"""Thin ctypes wrappers around the Win32 APIs the bot needs.

* window lookup (title / process name), client-area rectangle, focus checks
* input injection with ``SendInput`` using hardware scan codes, which games
  read reliably (virtual-key-only injection is often ignored by DirectX games)

Structures use fixed-width ctypes types so their layout can be unit-tested on
any OS; the functions themselves only work on Windows.
"""
from __future__ import annotations

import ctypes
import logging
import os
from dataclasses import dataclass

from . import keys as keynames
from .platform import IS_WINDOWS

log = logging.getLogger(__name__)

WORD = ctypes.c_uint16
DWORD = ctypes.c_uint32
LONG = ctypes.c_int32
ULONG_PTR = ctypes.c_size_t

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD),
                ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", DWORD), ("u", _INPUTUNION)]


class RECT(ctypes.Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    process: str
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.width, self.height


_U32 = None


def _user32():
    """user32 with explicit signatures (64-bit HWNDs must not be truncated)."""
    global _U32
    if not IS_WINDOWS:
        raise OSError("Win32 APIs are only available on Windows")
    if _U32 is None:
        u = ctypes.WinDLL("user32", use_last_error=True)
        H = ctypes.c_void_p
        for name, args, res in [
            ("IsWindowVisible", [H], ctypes.c_bool),
            ("IsIconic", [H], ctypes.c_bool),
            ("IsWindow", [H], ctypes.c_bool),
            ("GetWindowTextLengthW", [H], ctypes.c_int),
            ("GetWindowTextW", [H, ctypes.c_wchar_p, ctypes.c_int], ctypes.c_int),
            ("GetWindowThreadProcessId", [H, ctypes.POINTER(DWORD)], DWORD),
            ("GetClientRect", [H, ctypes.POINTER(RECT)], ctypes.c_bool),
            ("ClientToScreen", [H, ctypes.POINTER(POINT)], ctypes.c_bool),
            ("GetForegroundWindow", [], H),
            ("SetForegroundWindow", [H], ctypes.c_bool),
            ("ShowWindow", [H, ctypes.c_int], ctypes.c_bool),
            ("MapVirtualKeyW", [ctypes.c_uint, ctypes.c_uint], ctypes.c_uint),
            ("SetCursorPos", [ctypes.c_int, ctypes.c_int], ctypes.c_bool),
            ("GetCursorPos", [ctypes.POINTER(POINT)], ctypes.c_bool),
        ]:
            fn = getattr(u, name)
            fn.argtypes = args
            fn.restype = res
        _U32 = u
    return _U32


# ------------------------------------------------------------------ windows
def _process_name(pid: int) -> str:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [DWORD, ctypes.c_bool, DWORD]
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, DWORD, ctypes.c_wchar_p,
                                               ctypes.POINTER(DWORD)]
    k32.QueryFullProcessImageNameW.restype = ctypes.c_bool
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        k32.CloseHandle(h)


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    """(left, top, width, height) of the client area in screen pixels."""
    u = _user32()
    r = RECT()
    if not u.GetClientRect(hwnd, ctypes.byref(r)):
        raise OSError("GetClientRect failed")
    pt = POINT(0, 0)
    u.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, r.right - r.left, r.bottom - r.top


def list_windows() -> list[WindowInfo]:
    """Visible, titled top-level windows (excluding this process)."""
    u = _user32()
    out: list[WindowInfo] = []
    me = os.getpid()
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _lparam):
        hwnd = hwnd or 0
        try:
            if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
                return True
            n = u.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            pid = DWORD(0)
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == me:
                return True
            info = WindowInfo(int(hwnd), buf.value, _process_name(pid.value))
            try:
                info.left, info.top, info.width, info.height = client_rect(int(hwnd))
            except OSError:
                return True
            if info.width > 0 and info.height > 0:
                out.append(info)
        except Exception as exc:  # never let an exception escape into Win32
            log.debug("EnumWindows callback error: %s", exc)
        return True

    u.EnumWindows(EnumProc(cb), None)
    return out


# Windows that may carry the game's name in their title but are never the game.
NOT_THE_GAME = {"chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe",
                "vivaldi.exe", "iexplore.exe", "explorer.exe", "steamwebhelper.exe", "steam.exe",
                "discord.exe", "obs64.exe", "code.exe", "notepad.exe", "python.exe",
                "pythonw.exe", "windowsterminal.exe", "cmd.exe", "conhost.exe"}


def find_window(title_contains: str, process_name: str = "") -> WindowInfo | None:
    title_l = title_contains.strip().lower()
    proc_l = process_name.strip().lower()
    candidates = []
    for w in list_windows():
        if title_l and title_l not in w.title.lower():
            continue
        if proc_l:
            if w.process.lower() != proc_l:
                continue
        elif w.process.lower() in NOT_THE_GAME:
            continue
        candidates.append(w)
    if not candidates:
        return None
    # Prefer the biggest client area (the game rather than a small launcher).
    return max(candidates, key=lambda w: w.width * w.height)


def foreground_window() -> int:
    return int(_user32().GetForegroundWindow() or 0)


def focus_window(hwnd: int) -> bool:
    u = _user32()
    SW_RESTORE = 9
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, SW_RESTORE)
    return bool(u.SetForegroundWindow(hwnd))


def window_exists(hwnd: int) -> bool:
    return bool(_user32().IsWindow(hwnd))


# -------------------------------------------------------------------- input
class SendInputBackend:
    """Real keyboard/mouse injection through SendInput (scan codes)."""

    def __init__(self) -> None:
        u = _user32()
        self._send = u.SendInput
        self._send.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
        self._send.restype = ctypes.c_uint
        self._map = u.MapVirtualKeyW
        self._setpos = u.SetCursorPos
        self._getpos = u.GetCursorPos
        self._warned = False

    def _emit(self, inp: INPUT) -> None:
        sent = self._send(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        if sent != 1 and not self._warned:
            self._warned = True
            err = ctypes.get_last_error()
            log.error("SendInput was blocked (error %s). If the game runs as administrator, "
                      "run the bot as administrator too.", err)

    def _key(self, name: str, up: bool) -> None:
        vk = keynames.vk_code(name)
        scan = self._map(vk, 0) or keynames.fallback_scan_code(name)
        flags = KEYEVENTF_SCANCODE
        if keynames.is_extended(name):
            flags |= KEYEVENTF_EXTENDEDKEY
        if up:
            flags |= KEYEVENTF_KEYUP
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.ki = KEYBDINPUT(0, scan, flags, 0, 0)
        self._emit(inp)

    def key_down(self, name: str) -> None:
        self._key(name, up=False)

    def key_up(self, name: str) -> None:
        self._key(name, up=True)

    def move_to(self, x: int, y: int) -> None:
        self._setpos(int(x), int(y))
        # A zero relative move makes some engines refresh hover state.
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_MOVE, 0, 0)
        self._emit(inp)

    def mouse_down(self) -> None:
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0)
        self._emit(inp)

    def mouse_up(self) -> None:
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0)
        self._emit(inp)

    def cursor_pos(self) -> tuple[int, int]:
        pt = POINT()
        self._getpos(ctypes.byref(pt))
        return pt.x, pt.y
