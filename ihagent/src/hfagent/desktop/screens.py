"""Display layout in pointer space. Used for corners and bounds checks."""

from __future__ import annotations

import re
import subprocess
import sys

from .geometry import Monitor, appkit_frame_to_monitor


_XRANDR_MM = re.compile(r"(\d+)/\d+x(\d+)/\d+([+-]\d+)([+-]\d+)")
_XRANDR_PLAIN = re.compile(r"(?<![\d/])(\d+)x(\d+)([+-]\d+)([+-]\d+)")


def monitors_from_xrandr(text: str) -> list[Monitor]:
    """Parse ``xrandr --listmonitors`` (or a plain ``xrandr`` geometry)."""
    found: list[Monitor] = []
    for line in (text or "").splitlines():
        sized = _XRANDR_MM.search(line)
        if sized:
            width, height, origin_x, origin_y = sized.groups()
            found.append(Monitor(float(origin_x), float(origin_y), float(width), float(height), 1.0))
            continue
        plain = _XRANDR_PLAIN.search(line)
        if plain:
            width, height, origin_x, origin_y = plain.groups()
            found.append(Monitor(float(origin_x), float(origin_y), float(width), float(height), 1.0))
    return found


def ensure_dpi_aware() -> str:
    """Ask Windows to use physical pixels for this process.

    Returns ``aware``, ``already``, or ``unavailable``. UI Automation and
    pyautogui then share one space. If Windows has already locked the
    process, both APIs are still virtualized together, so element clicks
    stay consistent. Screenshot clicks do not use this flag: they measure
    the image.
    """
    if sys.platform != "win32":
        return "unavailable"
    import ctypes

    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        result = ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "aware" if int(result) == 0 else "already"
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            return "aware"
        except Exception:
            return "unavailable"


def read_monitors() -> list[Monitor]:
    """Best-effort display list. Empty means the layout is unknown."""
    try:
        if sys.platform == "darwin":
            return _mac_monitors()
        if sys.platform == "win32":
            return _windows_monitors()
        return _linux_monitors()
    except Exception:
        return []


def _mac_monitors() -> list[Monitor]:
    from AppKit import NSScreen

    screens = list(NSScreen.screens() or [])
    if not screens:
        return []
    primary = float(screens[0].frame().size.height)
    monitors: list[Monitor] = []
    for screen in screens:
        frame = screen.frame()
        try:
            scale = float(screen.backingScaleFactor())
        except Exception:
            scale = 1.0
        monitors.append(
            appkit_frame_to_monitor(
                float(frame.origin.x),
                float(frame.origin.y),
                float(frame.size.width),
                float(frame.size.height),
                primary,
                scale,
            )
        )
    return monitors


def _windows_monitors() -> list[Monitor]:
    import ctypes
    from ctypes import wintypes

    monitors: list[Monitor] = []

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.POINTER(RECT),
        ctypes.c_double,
    )

    def callback(hmon, hdc, rect, data):
        box = rect.contents
        monitors.append(
            Monitor(
                float(box.left),
                float(box.top),
                float(box.right - box.left),
                float(box.bottom - box.top),
                1.0,
            )
        )
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(0, 0, callback_type(callback), 0)
    return monitors


def _linux_monitors() -> list[Monitor]:
    try:
        proc = subprocess.run(
            ["xrandr", "--listmonitors"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return monitors_from_xrandr(proc.stdout or "")
