"""Click-through, always-on-top window flags. Toolkits are passed in.

The overlay paints. It does not take clicks and it does not capture the
screen. macOS uses an AppKit panel like the iheye bubble, with mouse
events ignored and window sharing turned off so a later screenshot does
not include the robot. Windows and Linux use a Qt tool window that does
not accept input, matching the iheye Qt port.
"""

from __future__ import annotations


MISSING_APPKIT = (
    "ihmouse overlay needs PyObjC. pip install 'ihagent[desktop]'. "
    "The system pointer still moves."
)
MISSING_QT = (
    "ihmouse overlay needs PySide6. pip install 'ihagent[desktop]'. "
    "The system pointer still moves."
)
MISSING_DISPLAY = (
    "ihmouse overlay needs a graphical session (no DISPLAY). "
    "The system pointer still moves."
)

# GetWindowLong GWL_EXSTYLE bits. OR them on; never replace the whole style.
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def module_available(name: str) -> bool:
    """True when ``name`` can be imported. Does not import it."""
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def backend_note(platform: str, environ: dict[str, str], available) -> str:
    """Empty when a GUI helper can be started. Otherwise a user-facing note."""
    if platform == "darwin":
        if not available("AppKit"):
            return MISSING_APPKIT
        return ""
    if not available("PySide6"):
        return MISSING_QT
    if platform.startswith("linux") and not environ.get("DISPLAY") and not environ.get("WAYLAND_DISPLAY"):
        return MISSING_DISPLAY
    return ""


def qt_window_flag_names() -> tuple[str, ...]:
    return (
        "FramelessWindowHint",
        "WindowStaysOnTopHint",
        "Tool",
        "WindowDoesNotAcceptFocus",
        "WindowTransparentForInput",
    )


def qt_widget_attributes() -> tuple[str, ...]:
    return (
        "WA_TranslucentBackground",
        "WA_ShowWithoutActivating",
        "WA_TransparentForMouseEvents",
    )


def apply_qt_overlay(widget, Qt) -> None:
    """Frameless, topmost, and transparent to input. ``Qt`` is PySide6.QtCore.Qt."""
    flag = 0
    for name in qt_window_flag_names():
        flag |= int(getattr(Qt.WindowType, name))
    widget.setWindowFlags(flag)
    for name in qt_widget_attributes():
        widget.setAttribute(getattr(Qt.WidgetAttribute, name), True)
    widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    extra = getattr(Qt.WidgetAttribute, "WA_X11DoNotAcceptFocus", None)
    if extra is not None:
        widget.setAttribute(extra, True)


def apply_appkit_panel(panel, *, clear_color, floating_level, sharing_none, behavior) -> None:
    """Borderless floating panel. Clicks pass through. Screenshots skip it."""
    panel.setLevel_(floating_level)
    panel.setCollectionBehavior_(behavior)
    panel.setFloatingPanel_(True)
    panel.setHidesOnDeactivate_(False)
    panel.setCanHide_(False)
    panel.setOpaque_(False)
    panel.setHasShadow_(False)
    panel.setBackgroundColor_(clear_color)
    panel.setIgnoresMouseEvents_(True)
    panel.setSharingType_(sharing_none)
    panel.setBecomesKeyOnlyIfNeeded_(True)
    panel.setWorksWhenModal_(True)


def windows_clickthrough_exstyle(current: int) -> int:
    """Add layered, transparent, no-activate tool bits to an existing exstyle."""
    return int(current) | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW


def windows_exclude_capture() -> int:
    """``WDA_EXCLUDEFROMCAPTURE``. The overlay is not part of a screen grab."""
    return WDA_EXCLUDEFROMCAPTURE


def apply_windows_extended(hwnd: int) -> None:
    """Best-effort click-through and capture exclusion. Failures are ignored."""
    if not hwnd:
        return
    try:
        import ctypes
    except ImportError:
        return
    try:
        user32 = ctypes.windll.user32
        get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        get_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        set_long.restype = ctypes.c_ssize_t
        current = int(get_long(int(hwnd), -20))
        set_long(int(hwnd), -20, windows_clickthrough_exstyle(current))
        user32.SetWindowDisplayAffinity(int(hwnd), windows_exclude_capture())
    except Exception:
        return
