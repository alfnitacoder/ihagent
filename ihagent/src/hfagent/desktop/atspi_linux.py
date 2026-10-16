"""Focused-window tree via AT-SPI (``pyatspi``).

``pyatspi`` is a distro package, not a pip extra. When it is missing the
snapshot carries :data:`LINUX_ATSPI_HINT` and clicks fall back to vision.
Desktop coordinates from AT-SPI are already pointer pixels on X11.
"""

from __future__ import annotations

import os
from typing import Any

from .elements import TreeSnapshot, elements_from_raw, focused_from_raw, walk_nodes
from .geometry import Rect
from .permissions import LINUX_ATSPI_HINT


def read_tree() -> TreeSnapshot:
    try:
        import pyatspi
    except ImportError:
        return TreeSnapshot(error=_hint())
    try:
        desktop = pyatspi.Registry.getDesktop(0)
    except Exception as exc:
        return TreeSnapshot(error=f"{_hint()} ({exc})")
    frame = _active_frame(pyatspi, desktop)
    if frame is None:
        return TreeSnapshot(error="")
    rows = walk_nodes(
        frame,
        _children,
        lambda node: _attrs(pyatspi, node),
    )
    title = ""
    try:
        title = str(frame.name or "")
    except Exception:
        title = ""
    return TreeSnapshot(
        app=title,
        elements=elements_from_raw(rows),
        window=_rect(pyatspi, frame),
        focused=_focused(pyatspi),
    )


def _hint() -> str:
    extra = ""
    if os.environ.get("WAYLAND_DISPLAY"):
        extra = " This session is Wayland; AT-SPI still works for many GTK and Qt apps."
    return LINUX_ATSPI_HINT + extra


def _active_frame(pyatspi: Any, desktop: Any) -> Any:
    try:
        apps = list(desktop)
    except Exception:
        return None
    for app in apps:
        try:
            children = list(app)
        except Exception:
            continue
        for child in children:
            try:
                if child.getState().contains(pyatspi.STATE_ACTIVE):
                    return child
            except Exception:
                continue
    return None


def _children(node: Any) -> list[Any]:
    try:
        return list(node)
    except Exception:
        return []


def _attrs(pyatspi: Any, node: Any) -> dict[str, Any] | None:
    try:
        role = node.getRoleName() or ""
        name = node.name or ""
        password = node.getRole() == pyatspi.ROLE_PASSWORD_TEXT
        component = node.queryComponent()
        extents = component.getExtents(pyatspi.DESKTOP_COORDS)
        return {
            "role": role,
            "name": name,
            "x": float(extents.x),
            "y": float(extents.y),
            "width": float(extents.width),
            "height": float(extents.height),
            "password": password,
        }
    except Exception:
        return None


def _rect(pyatspi: Any, node: Any) -> Rect | None:
    raw = _attrs(pyatspi, node)
    if not raw or raw["width"] <= 0 or raw["height"] <= 0:
        return None
    return Rect(raw["x"], raw["y"], raw["width"], raw["height"])


def _focused(pyatspi: Any) -> Any:
    getter = getattr(pyatspi.Registry, "getFocus", None)
    if getter is None:
        return None
    try:
        node = getter()
    except Exception:
        return None
    return focused_from_raw(_attrs(pyatspi, node) if node is not None else None)
