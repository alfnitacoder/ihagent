"""Map pointer space onto one click-through window that spans every display.

Pointer space has its origin at the top-left of the primary display and y
grows downward. A display to the left has a negative x. The window uses
that same space. HiDPI scale is not applied: accessibility bounds and the
pointer are already in points on macOS and in logical pixels on Windows
and Linux. The AppKit window frame is the exception, because AppKit's
origin is the bottom-left of the primary display.
"""

from __future__ import annotations

from .geometry import Monitor, Rect, appkit_frame_to_monitor


def virtual_rect(monitors: list[Monitor]) -> Rect | None:
    """Union of every display in pointer space. Scale is ignored on purpose."""
    if not monitors:
        return None
    left = min(monitor.x for monitor in monitors)
    top = min(monitor.y for monitor in monitors)
    right = max(monitor.x + monitor.width for monitor in monitors)
    bottom = max(monitor.y + monitor.height for monitor in monitors)
    return Rect(left, top, right - left, bottom - top)


def pointer_to_view(x: float, y: float, virtual: Rect) -> tuple[float, float]:
    """View-local point. The view is y-down, including the flipped AppKit view."""
    return (float(x) - virtual.x, float(y) - virtual.y)


def appkit_window_frame(virtual: Rect, primary_height: float) -> tuple[float, float, float, float]:
    """``(x, y, w, h)`` for ``setFrame_``, y growing upward from the primary bottom."""
    return (
        virtual.x,
        float(primary_height) - (virtual.y + virtual.height),
        virtual.width,
        virtual.height,
    )


def qt_window_geometry(virtual: Rect) -> tuple[float, float, float, float]:
    """Qt global geometry. Negative origins are kept for monitors left of the primary."""
    return (virtual.x, virtual.y, virtual.width, virtual.height)


def monitors_from_appkit_frames(
    frames: list[tuple[float, float, float, float]],
    scales: list[float] | None = None,
) -> tuple[list[Monitor], float]:
    """AppKit screen frames ``(x, y, w, h)`` to pointer-space monitors.

    The first frame is the primary display. Returns ``(monitors, primary_height)``.
    """
    if not frames:
        return [], 0.0
    px, py, _pw, ph = frames[0]
    primary_height = float(py) + float(ph)
    found: list[Monitor] = []
    for index, (x, y, width, height) in enumerate(frames):
        scale = 1.0
        if scales is not None and index < len(scales):
            scale = float(scales[index] or 1.0)
        found.append(appkit_frame_to_monitor(x, y, width, height, primary_height, scale))
    return found, primary_height


def monitors_from_qt_rects(
    rects: list[tuple[float, float, float, float]],
    scales: list[float] | None = None,
) -> list[Monitor]:
    """Qt screen geometries are already pointer space."""
    found: list[Monitor] = []
    for index, (x, y, width, height) in enumerate(rects):
        scale = 1.0
        if scales is not None and index < len(scales):
            scale = float(scales[index] or 1.0)
        found.append(Monitor(float(x), float(y), float(width), float(height), scale))
    return found


LABEL_HEIGHT = 22.0


def label_size(text: str) -> tuple[float, float]:
    return (7.4 * max(len(text), 1) + 16.0, LABEL_HEIGHT)


def label_box(bounds: Rect, text: str, virtual: Rect | None = None) -> Rect:
    """Sit above the target. Flip below when that would leave the displays."""
    width, height = label_size(text)
    x = bounds.x
    y = bounds.y - height - 6.0
    if virtual is not None and y < virtual.y + 2.0:
        y = bounds.y + bounds.height + 6.0
    if virtual is not None:
        limit = virtual.x + virtual.width - width - 2.0
        x = min(max(x, virtual.x + 2.0), max(virtual.x + 2.0, limit))
    return Rect(x, y, width, height)


def monitor_views(monitors: list[Monitor]) -> list[Rect]:
    """One overlay rect per display.

    A single window stretched across every display mis-scales when one
    screen is Retina and another is not: AppKit picks one backing scale
    for the whole frame. Each display gets its own panel in that
    display's points instead.
    """
    views: list[Rect] = []
    for monitor in monitors:
        if monitor.width <= 0 or monitor.height <= 0:
            continue
        views.append(Rect(monitor.x, monitor.y, monitor.width, monitor.height))
    return views


def _shift_points(item: dict, dx: float, dy: float) -> None:
    raw = item.get("points")
    if not raw:
        return
    item["points"] = [(float(px) + dx, float(py) + dy) for px, py in raw]


def place_ops(ops: list[dict], virtual: Rect) -> list[dict]:
    """Shift pointer-space draw ops into the overlay view."""
    placed: list[dict] = []
    for op in ops:
        item = dict(op)
        for key in ("x", "cx", "x1", "x2"):
            if key in item:
                item[key] = float(item[key]) - virtual.x
        for key in ("y", "cy", "y1", "y2"):
            if key in item:
                item[key] = float(item[key]) - virtual.y
        _shift_points(item, -virtual.x, -virtual.y)
        placed.append(item)
    return placed
