"""Pointer coordinates, HiDPI scale, and multi-monitor corners.

The pointer space used everywhere else in this package has its origin at
the top-left of the primary display, with y increasing downward. A display
to the left or above the primary has a negative origin. macOS accessibility
and ``screencapture`` speak in points; a Retina capture is twice as many
pixels. Windows and Linux pointer APIs speak in pixels once the process
DPI awareness matches UI Automation. Vision models speak in pixels of the
image they were sent. :func:`pixels_to_pointer` is the conversion.
"""

from __future__ import annotations

from dataclasses import dataclass


CORNER_MARGIN = 5.0


@dataclass(frozen=True)
class Rect:
    """A box in pointer space."""

    x: float
    y: float
    width: float
    height: float

    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)


@dataclass(frozen=True)
class Monitor:
    """One display in pointer space. ``scale`` is pixels per point, informational."""

    x: float
    y: float
    width: float
    height: float
    scale: float = 1.0


@dataclass(frozen=True)
class CaptureRegion:
    """A screenshot's place in pointer space, plus the image's pixel size.

    ``width`` and ``height`` are pointer units (points on macOS, pixels on
    Windows and Linux). ``pixel_width`` and ``pixel_height`` are the image
    the vision model actually sees. On a 2x Retina window those pixel
    dimensions are twice the point size. On a 150% Windows display they
    are 1.5 times the logical size when the grabber returns physical pixels
    and the rectangle is logical. The ratio is measured, not assumed.
    """

    origin_x: float
    origin_y: float
    width: float
    height: float
    pixel_width: int
    pixel_height: int

    @classmethod
    def from_rect(cls, rect: Rect, pixel_width: int, pixel_height: int) -> "CaptureRegion":
        return cls(rect.x, rect.y, rect.width, rect.height, int(pixel_width), int(pixel_height))


def pixels_to_pointer(region: CaptureRegion, px: float, py: float) -> tuple[float, float]:
    """Map a point in the screenshot to pointer space.

    ``px`` and ``py`` are pixels of the image, origin top-left, and may sit
    on the far edge (``pixel_width``, ``pixel_height``). Raises ``ValueError``
    when the point is outside the image or the image has no size.
    """
    if region.pixel_width <= 0 or region.pixel_height <= 0:
        raise ValueError("screenshot has no pixel size")
    if region.width <= 0 or region.height <= 0:
        raise ValueError("screenshot has no screen size")
    if px < 0 or py < 0 or px > region.pixel_width or py > region.pixel_height:
        raise ValueError(
            f"point ({px}, {py}) is outside the screenshot "
            f"({region.pixel_width}x{region.pixel_height})"
        )
    return (
        region.origin_x + float(px) * (region.width / float(region.pixel_width)),
        region.origin_y + float(py) * (region.height / float(region.pixel_height)),
    )


def scale_rect(rect: Rect, scale: float) -> Rect:
    """Divide a physical-pixel rectangle by a HiDPI scale into pointer units.

    A scale of 2 turns an 800x600 pixel box at (200, 100) into a 400x300
    point box at (100, 50). Scale 1 returns the same rectangle. Element
    clicks do not need this when the accessibility backend and the pointer
    already share a space. Screenshot clicks use :func:`pixels_to_pointer`,
    which measures the scale from the image instead of trusting a constant.
    """
    if scale <= 0:
        raise ValueError("scale must be positive")
    if scale == 1:
        return rect
    return Rect(rect.x / scale, rect.y / scale, rect.width / scale, rect.height / scale)


def appkit_frame_to_monitor(
    origin_x: float,
    origin_y: float,
    width: float,
    height: float,
    primary_height: float,
    scale: float = 1.0,
) -> Monitor:
    """Convert one AppKit screen frame to pointer space.

    AppKit's origin is the bottom-left of the primary display and y grows
    upward. The pointer origin is the top-left of that display and y grows
    downward. ``primary_height`` is the main display's height in points.
    """
    top = primary_height - (origin_y + height)
    return Monitor(float(origin_x), float(top), float(width), float(height), float(scale or 1.0))


def corner_points(monitors: list[Monitor]) -> list[tuple[float, float]]:
    """Every display corner, plus ``(0, 0)`` (pyautogui's built-in failsafe)."""
    points = [(0.0, 0.0)]
    for monitor in monitors:
        right = monitor.x + monitor.width
        bottom = monitor.y + monitor.height
        points.extend(
            (
                (monitor.x, monitor.y),
                (right, monitor.y),
                (monitor.x, bottom),
                (right, bottom),
            )
        )
    return points


def is_corner(
    x: float,
    y: float,
    monitors: list[Monitor],
    margin: float = CORNER_MARGIN,
) -> bool:
    """True when ``(x, y)`` is within ``margin`` of a screen corner."""
    for cx, cy in corner_points(monitors):
        if abs(x - cx) <= margin and abs(y - cy) <= margin:
            return True
    return False


def on_screen(x: float, y: float, monitors: list[Monitor], margin: float = 2.0) -> bool:
    """True when the point lands on a display. An empty list means unknown."""
    if not monitors:
        return True
    return any(
        monitor.x - margin <= x <= monitor.x + monitor.width + margin
        and monitor.y - margin <= y <= monitor.y + monitor.height + margin
        for monitor in monitors
    )
