"""Remember where the user dragged the iheye bubble, and where its card sits.

Coordinates are AppKit points: origin at the bottom-left, y grows upward.
A display to the left or below the menu-bar display has a negative origin.
Companion panels must be clamped to the display that holds the bubble, not
to the main display.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScreenFrame:
    """One rectangle in global AppKit coordinates."""

    x: float
    y: float
    w: float
    h: float

    def contains_point(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def intersection_area(self, x: float, y: float, w: float, h: float) -> float:
        left = max(self.x, x)
        bottom = max(self.y, y)
        right = min(self.x + self.w, x + w)
        top = min(self.y + self.h, y + h)
        if right <= left or top <= bottom:
            return 0.0
        return (right - left) * (top - bottom)


@dataclass(frozen=True)
class Display:
    """A physical display. ``frame`` is the full panel; ``visible`` excludes the menu bar and Dock."""

    frame: ScreenFrame
    visible: ScreenFrame


@dataclass(frozen=True)
class SavedPlace:
    x: float
    y: float
    screen: ScreenFrame | None = None


@dataclass(frozen=True)
class Placement:
    """Where to put the bubble, and the usable rectangle of the display that owns it."""

    x: float
    y: float
    visible: ScreenFrame


def load_position(path: Path | str) -> tuple[float, float] | None:
    place = load_placement(path)
    if place is None:
        return None
    return place.x, place.y


def load_placement(path: Path | str) -> SavedPlace | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        x = float(data["x"])
        y = float(data["y"])
    except (KeyError, TypeError, ValueError):
        return None
    return SavedPlace(x, y, _screen_from_payload(data.get("screen")))


def save_position(
    path: Path | str,
    x: float,
    y: float,
    screen: ScreenFrame | None = None,
) -> None:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {"x": x, "y": y}
    if screen is not None:
        payload["screen"] = {"x": screen.x, "y": screen.y, "w": screen.w, "h": screen.h}
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    tmp.replace(dest)


def clamp_position(
    x: float,
    y: float,
    origin_x: float,
    origin_y: float,
    width: float,
    height: float,
    panel_w: float,
    panel_h: float,
) -> tuple[float, float]:
    """Keep a panel inside a screen rectangle. Origin is the bottom-left."""
    max_x = origin_x + max(0.0, width - panel_w)
    max_y = origin_y + max(0.0, height - panel_h)
    return min(max(x, origin_x), max_x), min(max(y, origin_y), max_y)


def drop_origin(
    eye_x: float,
    eye_y: float,
    eye_w: float,
    eye_h: float,
    drop_w: float,
    drop_h: float,
    origin_x: float,
    origin_y: float,
    width: float,
    height: float,
    gap: float = 6.0,
) -> tuple[float, float]:
    """Place a companion card on the bubble's display.

    ``origin_*`` / ``width`` / ``height`` are that display's visible frame,
    in global coordinates (negative when the display sits left or below).
    The card prefers to sit centered under the bubble. It flips above when
    the bottom edge is too close, and shifts left (or right) when a side
    edge is too close, then clamps so it cannot spill onto another display.
    """
    screen_left = origin_x
    screen_bottom = origin_y
    screen_right = origin_x + width
    screen_top = origin_y + height

    below = eye_y - drop_h - gap
    above = eye_y + eye_h + gap
    if below >= screen_bottom:
        y = below
    elif above + drop_h <= screen_top:
        y = above
    else:
        room_below = eye_y - screen_bottom
        room_above = screen_top - (eye_y + eye_h)
        y = below if room_below >= room_above else above

    centered = eye_x + (eye_w - drop_w) / 2.0
    if centered + drop_w > screen_right:
        x = eye_x + eye_w - drop_w
    elif centered < screen_left:
        x = eye_x
    else:
        x = centered
    return clamp_position(x, y, origin_x, origin_y, width, height, drop_w, drop_h)


def default_position(
    origin_x: float,
    origin_y: float,
    width: float,
    height: float,
    panel_w: float,
    panel_h: float,
    margin: float = 18.0,
) -> tuple[float, float]:
    """Top-right of the visible screen, in AppKit coordinates (origin bottom-left)."""
    x = origin_x + width - panel_w - margin
    y = origin_y + height - panel_h - margin
    return clamp_position(x, y, origin_x, origin_y, width, height, panel_w, panel_h)


def frames_match(a: ScreenFrame, b: ScreenFrame, eps: float = 1.0) -> bool:
    return (
        abs(a.x - b.x) <= eps
        and abs(a.y - b.y) <= eps
        and abs(a.w - b.w) <= eps
        and abs(a.h - b.h) <= eps
    )


def display_containing_point(displays: list[Display], px: float, py: float) -> Display | None:
    """Display whose full frame contains the point. Shared edges belong to the display on the top/right."""
    hits = [item for item in displays if item.frame.contains_point(px, py)]
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    return max(hits, key=lambda item: item.frame.w * item.frame.h)


def display_for_rect(
    displays: list[Display],
    x: float,
    y: float,
    w: float,
    h: float,
) -> Display | None:
    """Display that owns a window rectangle. The center wins; otherwise the greatest overlap."""
    if w < 0 or h < 0:
        return None
    center = display_containing_point(displays, x + w / 2.0, y + h / 2.0)
    if center is not None:
        return center
    best: Display | None = None
    best_area = 0.0
    for item in displays:
        area = item.frame.intersection_area(x, y, w, h)
        if area > best_area:
            best = item
            best_area = area
    return best


def restore_bubble(
    saved: SavedPlace | None,
    displays: list[Display],
    mouse: tuple[float, float] | None,
    main: Display | None,
    panel_w: float,
    panel_h: float,
) -> Placement:
    """Put the bubble back on the display that had it.

    A saved origin that still lies on a connected display is clamped to that
    display's visible frame, even when the origin is negative. If that
    display is gone, the bubble uses the display under the pointer, then the
    main display, at the usual top-right corner.
    """
    pool = list(displays)
    if main is not None and not any(frames_match(main.frame, item.frame) for item in pool):
        pool.append(main)
    if not pool:
        visible = ScreenFrame(0.0, 0.0, 1440.0, 900.0)
        return Placement(40.0, 40.0, visible)

    def fallback() -> Display:
        if mouse is not None:
            hit = display_containing_point(pool, mouse[0], mouse[1])
            if hit is not None:
                return hit
        if main is not None:
            return main
        return pool[0]

    if saved is None:
        screen = main if main is not None else fallback()
        return _placement_on(screen, None, panel_w, panel_h, use_default=True)

    hit = display_for_rect(pool, saved.x, saved.y, panel_w, panel_h)
    if hit is None and saved.screen is not None:
        hit = next((item for item in pool if frames_match(item.frame, saved.screen)), None)
    if hit is not None:
        return _placement_on(hit, saved, panel_w, panel_h, use_default=False)
    return _placement_on(fallback(), None, panel_w, panel_h, use_default=True)


def _placement_on(
    screen: Display,
    saved: SavedPlace | None,
    panel_w: float,
    panel_h: float,
    *,
    use_default: bool,
) -> Placement:
    visible = screen.visible
    if use_default or saved is None:
        x, y = default_position(visible.x, visible.y, visible.w, visible.h, panel_w, panel_h)
    else:
        x, y = clamp_position(
            saved.x,
            saved.y,
            visible.x,
            visible.y,
            visible.w,
            visible.h,
            panel_w,
            panel_h,
        )
    return Placement(x, y, visible)


def _screen_from_payload(value: object) -> ScreenFrame | None:
    if not isinstance(value, dict):
        return None
    try:
        frame = ScreenFrame(float(value["x"]), float(value["y"]), float(value["w"]), float(value["h"]))
    except (KeyError, TypeError, ValueError):
        return None
    if frame.w <= 0 or frame.h <= 0:
        return None
    return frame
