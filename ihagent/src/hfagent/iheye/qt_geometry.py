"""Map Qt screen coordinates onto the AppKit frames iheye already uses.

Qt's origin is the top-left and y grows downward. The placement helpers in
``position`` use a bottom-left origin. Cards are placed in that space, on the
display that holds the bubble, then converted back so the Qt window stays on
the same monitor.
"""

from __future__ import annotations

from dataclasses import dataclass

from .position import Display, ScreenFrame, drop_origin


@dataclass(frozen=True)
class QtRect:
    x: float
    y: float
    w: float
    h: float

    def contains_point(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


def primary_bottom(primary: QtRect) -> float:
    """Qt y of the primary screen's bottom edge. AppKit's y=0 sits here."""
    return float(primary.y) + float(primary.h)


def qt_origin_to_appkit(x: float, y: float, height: float, primary_bottom_y: float) -> tuple[float, float]:
    """Top-left Qt origin of a window → bottom-left AppKit origin."""
    return float(x), float(primary_bottom_y) - (float(y) + float(height))


def appkit_origin_to_qt(x: float, y: float, height: float, primary_bottom_y: float) -> tuple[float, float]:
    return float(x), float(primary_bottom_y) - (float(y) + float(height))


def qt_point_to_appkit(x: float, y: float, primary_bottom_y: float) -> tuple[float, float]:
    return float(x), float(primary_bottom_y) - float(y)


def _frame(rect: QtRect, primary_bottom_y: float) -> ScreenFrame:
    ax, ay = qt_origin_to_appkit(rect.x, rect.y, rect.h, primary_bottom_y)
    return ScreenFrame(ax, ay, rect.w, rect.h)


def qt_screen_to_display(full: QtRect, visible: QtRect, primary_bottom_y: float) -> Display:
    return Display(
        frame=_frame(full, primary_bottom_y),
        visible=_frame(visible, primary_bottom_y),
    )


def place_card(
    bubble: QtRect,
    card_w: float,
    card_h: float,
    visible: ScreenFrame,
    primary_bottom_y: float,
    gap: float = 6.0,
) -> QtRect:
    """Companion card on the bubble's display, in Qt coordinates."""
    ax, ay = qt_origin_to_appkit(bubble.x, bubble.y, bubble.h, primary_bottom_y)
    dx, dy = drop_origin(
        ax,
        ay,
        bubble.w,
        bubble.h,
        card_w,
        card_h,
        visible.x,
        visible.y,
        visible.w,
        visible.h,
        gap,
    )
    qx, qy = appkit_origin_to_qt(dx, dy, card_h, primary_bottom_y)
    return QtRect(qx, qy, card_w, card_h)


def screen_owning_point(screens: list[QtRect], px: float, py: float) -> QtRect | None:
    hits = [item for item in screens if item.contains_point(px, py)]
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    return max(hits, key=lambda item: item.w * item.h)


def same_monitor(a: QtRect, b: QtRect, screens: list[QtRect]) -> bool:
    """True when both rectangles' centers sit on one screen."""
    left = screen_owning_point(screens, a.x + a.w / 2.0, a.y + a.h / 2.0)
    right = screen_owning_point(screens, b.x + b.w / 2.0, b.y + b.h / 2.0)
    if left is None or right is None:
        return False
    return left == right
