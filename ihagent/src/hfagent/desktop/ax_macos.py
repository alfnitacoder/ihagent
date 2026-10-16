"""Focused-app accessibility tree via the macOS AX API.

Imported only when a mouse tool runs on Darwin. Coordinate space matches
the pointer: top-left origin, y downward, units in points.
"""

from __future__ import annotations

from typing import Any

from .elements import TreeSnapshot, elements_from_raw, focused_from_raw, walk_nodes
from .geometry import Rect
from .permissions import MAC_AX_HINT, MAC_AX_INSTALL


def read_tree() -> TreeSnapshot:
    try:
        import ApplicationServices as app_services
    except ImportError:
        return TreeSnapshot(error=MAC_AX_INSTALL)
    trusted = _trusted(app_services)
    if trusted is False:
        return TreeSnapshot(error=MAC_AX_HINT)
    try:
        system = app_services.AXUIElementCreateSystemWide()
        app = _attr(app_services, system, "AXFocusedApplication")
        if app is None:
            return TreeSnapshot(error="Error: no focused application.")
        window = _attr(app_services, app, "AXFocusedWindow")
        root = window if window is not None else app
        rows = walk_nodes(
            root,
            lambda node: _children(app_services, node),
            lambda node: _element_dict(app_services, node),
        )
        focused = focused_from_raw(
            _element_dict(app_services, _attr(app_services, system, "AXFocusedUIElement"))
        )
        title = _attr(app_services, app, "AXTitle") or ""
        return TreeSnapshot(
            app=str(title),
            elements=elements_from_raw(rows),
            window=_rect_of(app_services, window) if window is not None else None,
            focused=focused,
        )
    except Exception as exc:
        return TreeSnapshot(error=f"Error: macOS accessibility failed ({exc}).")


def _trusted(app_services: Any) -> bool | None:
    try:
        return bool(app_services.AXIsProcessTrusted())
    except Exception:
        return None


def _attr(app_services: Any, element: Any, name: str) -> Any:
    if element is None:
        return None
    try:
        result = app_services.AXUIElementCopyAttributeValue(element, name, None)
    except Exception:
        return None
    if isinstance(result, tuple):
        if len(result) >= 2 and result[0] not in (0, None, True):
            # A non-zero AXError means the attribute is missing.
            if isinstance(result[0], int) and result[0] != 0:
                return None
        return result[-1]
    return result


def _children(app_services: Any, element: Any) -> list[Any]:
    children = _attr(app_services, element, "AXChildren")
    if not children:
        return []
    try:
        return list(children)
    except TypeError:
        return []


def _element_dict(app_services: Any, element: Any) -> dict[str, Any] | None:
    if element is None:
        return None
    role = _attr(app_services, element, "AXRole") or ""
    subrole = _attr(app_services, element, "AXSubrole") or ""
    name = _attr(app_services, element, "AXTitle") or _attr(app_services, element, "AXDescription") or ""
    if not name:
        name = _attr(app_services, element, "AXValue") or ""
        if not isinstance(name, str):
            name = ""
    rect = _rect_of(app_services, element)
    password = "secure" in str(role).lower() or "secure" in str(subrole).lower()
    if rect is None:
        return {
            "role": str(subrole or role),
            "name": str(name),
            "x": 0,
            "y": 0,
            "width": 0,
            "height": 0,
            "password": password,
        }
    return {
        "role": str(subrole or role),
        "name": str(name),
        "x": rect.x,
        "y": rect.y,
        "width": rect.width,
        "height": rect.height,
        "password": password,
    }


def _rect_of(app_services: Any, element: Any) -> Rect | None:
    position = _unpack_point(_attr(app_services, element, "AXPosition"))
    size = _unpack_size(_attr(app_services, element, "AXSize"))
    if position is None or size is None:
        return None
    return Rect(position[0], position[1], size[0], size[1])


def _unpack_point(value: Any) -> tuple[float, float] | None:
    return _unpack(value, "point")


def _unpack_size(value: Any) -> tuple[float, float] | None:
    return _unpack(value, "size")


def _unpack(value: Any, kind: str) -> tuple[float, float] | None:
    if value is None:
        return None
    if hasattr(value, "x") and hasattr(value, "y"):
        return float(value.x), float(value.y)
    if hasattr(value, "width") and hasattr(value, "height"):
        return float(value.width), float(value.height)
    try:
        from Quartz import AXValueGetValue, kAXValueCGPointType, kAXValueCGSizeType
    except ImportError:
        return None
    code = kAXValueCGPointType if kind == "point" else kAXValueCGSizeType
    try:
        result = AXValueGetValue(value, code, None)
    except Exception:
        return None
    point = result[1] if isinstance(result, tuple) and len(result) >= 2 else result
    if point is None:
        return None
    if kind == "point" and hasattr(point, "x"):
        return float(point.x), float(point.y)
    if kind == "size" and hasattr(point, "width"):
        return float(point.width), float(point.height)
    return None
