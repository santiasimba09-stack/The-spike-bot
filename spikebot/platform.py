"""Small helpers for platform-specific behaviour.

Everything Windows-only is imported lazily so the rest of the package (and the
test-suite) runs on any OS.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

_dpi_done = False


def set_dpi_awareness() -> None:
    """Make the process DPI aware so screenshots and clicks share one coordinate space.

    Must run before any window is created and before screen metrics are read
    (i.e. before Tk, mss or input code initialises). Safe to call repeatedly.
    """
    global _dpi_done
    if _dpi_done or not IS_WINDOWS:
        _dpi_done = True
        return
    import ctypes

    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception as exc:  # pragma: no cover - very old Windows
            log.warning("Could not set DPI awareness: %s", exc)
    _dpi_done = True
