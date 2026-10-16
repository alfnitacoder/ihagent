"""Focused-window tree via pywinauto (UI Automation), with uiautomation as a fallback.

Bounds are the process coordinate space. :func:`ensure_dpi_aware` runs
first so UI Automation and pyautogui agree on HiDPI displays. If Windows
has already locked DPI awareness, both APIs stay in that same space.
"""

from __future__ import annotations

from typing import Any

from .elements import TreeSnapshot, elements_from_raw, focused_from_raw, walk_nodes
from .geometry import Rect
from .permissions import WIN_UIA_HINT
from .screens import ensure_dpi_aware

# UIA_IsPasswordPropertyId
_IS_PASSWORD = 30019


def read_tree() -> TreeSnapshot:
    ensure_dpi_aware()
    try:
        from pywinauto import Desktop
    except ImportError:
        return _read_uiautomation()
    hwnd = _foreground_hwnd()
    if isinstance(hwnd, str):
        return TreeSnapshot(error=hwnd)
    try:
        window = Desktop(backend="uia").window(handle=hwnd)
        wrapper = window.wrapper_object()
    except Exception as exc:
        return TreeSnapshot(error=f"{WIN_UIA_HINT} ({exc})")
    rows = walk_nodes(wrapper, _children, _attrs)
    title = ""
    try:
        title = wrapper.window_text() or ""
    except Exception:
        title = ""
    return TreeSnapshot(
        app=str(title),
        elements=elements_from_raw(rows),
        window=_bounds(wrapper),
        focused=_focused_pywinauto(),
    )


def _foreground_hwnd() -> int | str:
    try:
        import ctypes

        hwnd = int(ctypes.windll.user32.GetForegroundWindow())
    except Exception as exc:
        return f"Error: could not find the foreground window ({exc})."
    if not hwnd:
        return "Error: no foreground window."
    return hwnd


def _children(wrapper: Any) -> list[Any]:
    try:
        return list(wrapper.children())
    except Exception:
        return []


def _attrs(wrapper: Any) -> dict[str, Any] | None:
    try:
        info = wrapper.element_info
        rect = info.rectangle
        return {
            "role": info.control_type or "",
            "name": info.name or "",
            "x": float(rect.left),
            "y": float(rect.top),
            "width": float(rect.width()),
            "height": float(rect.height()),
            "password": _is_password(wrapper),
        }
    except Exception:
        return None


def _is_password(wrapper: Any) -> bool:
    try:
        element = wrapper.element_info.element
        if element.GetCurrentPropertyValue(_IS_PASSWORD):
            return True
    except Exception:
        pass
    return False


def _bounds(wrapper: Any) -> Rect | None:
    raw = _attrs(wrapper)
    if not raw or raw["width"] <= 0 or raw["height"] <= 0:
        return None
    return Rect(raw["x"], raw["y"], raw["width"], raw["height"])


def _focused_pywinauto() -> Any:
    try:
        from pywinauto import Desktop

        wrapper = Desktop(backend="uia").get_focus()
    except Exception:
        return None
    if wrapper is None:
        return None
    return focused_from_raw(_attrs(wrapper))


def _read_uiautomation() -> TreeSnapshot:
    try:
        import uiautomation as auto
    except ImportError:
        return TreeSnapshot(error=WIN_UIA_HINT)
    try:
        control = auto.GetForegroundControl()
    except Exception as exc:
        return TreeSnapshot(error=f"{WIN_UIA_HINT} ({exc})")
    if control is None:
        return TreeSnapshot(error="Error: no foreground window.")
    rows = walk_nodes(control, _auto_children, _auto_attrs)
    rect = _auto_rect(control)
    focused = focused_from_raw(_auto_attrs(getattr(auto, "GetFocusedControl", lambda: None)()))
    return TreeSnapshot(
        app=str(getattr(control, "Name", "") or ""),
        elements=elements_from_raw(rows),
        window=rect,
        focused=focused,
    )


def _auto_children(control: Any) -> list[Any]:
    try:
        return list(control.GetChildren())
    except Exception:
        return []


def _auto_attrs(control: Any) -> dict[str, Any] | None:
    if control is None:
        return None
    try:
        rect = control.BoundingRectangle
        password = False
        try:
            password = bool(control.GetPropertyValue(_IS_PASSWORD))
        except Exception:
            password = False
        return {
            "role": getattr(control, "ControlTypeName", "") or "",
            "name": getattr(control, "Name", "") or "",
            "x": float(rect.left),
            "y": float(rect.top),
            "width": float(rect.right - rect.left),
            "height": float(rect.bottom - rect.top),
            "password": password,
        }
    except Exception:
        return None


def _auto_rect(control: Any) -> Rect | None:
    raw = _auto_attrs(control)
    if not raw or raw["width"] <= 0 or raw["height"] <= 0:
        return None
    return Rect(raw["x"], raw["y"], raw["width"], raw["height"])
