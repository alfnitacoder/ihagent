"""Where the pupil looks, and how fast the overlay should redraw.

Mouse gaze uses screen points. Keyboard reaction is only a timestamp:
the handler below never reads which key was pressed.
"""

from __future__ import annotations

import math

# Matches the old 30fps tick, which advanced phase by 0.12 each frame.
PHASE_PER_SEC = 3.6

KEYBOARD_HINT = (
    "iheye: keyboard glances need Accessibility permission "
    "(System Settings → Privacy & Security → Accessibility). "
    "Enable the Python that runs ihagent. Mouse tracking works without it."
)


def gaze_from_points(
    eye_x: float,
    eye_y: float,
    mouse_x: float,
    mouse_y: float,
    max_radius: float = 4.2,
    reach: float = 420.0,
) -> tuple[float, float]:
    """Pupil offset toward the pointer. Both axes use AppKit's bottom-left origin."""
    dx = mouse_x - eye_x
    dy = mouse_y - eye_y
    dist = math.hypot(dx, dy)
    if dist < 1.0 or max_radius <= 0:
        return (0.0, 0.0)
    scale = min(1.0, dist / reach) * max_radius / dist
    return (dx * scale, dy * scale)


def typing_reaction(age: float | None, phase: float) -> tuple[tuple[float, float], float]:
    """Glance and blink from how long ago a key went down.

    ``age`` is seconds since the last key activity, or None. The characters
    are not an input: callers must not pass them in.
    """
    if age is None or age < 0 or age > 1.4:
        return (0.0, 0.0), 0.0
    blink = 0.0
    if age < 0.14:
        blink = math.sin((age / 0.14) * math.pi)
    glance = math.exp(-age * 3.0)
    ox = math.sin(phase * 9.0) * 1.4 * glance
    oy = -0.8 * glance
    return (ox, oy), blink


def tracked_pupil(
    state_offset: tuple[float, float],
    gaze: tuple[float, float],
    typing_offset: tuple[float, float] = (0.0, 0.0),
) -> tuple[float, float]:
    """Gaze leads. A little of the state's own motion stays so thinking still stirs."""
    return (
        state_offset[0] * 0.2 + gaze[0] + typing_offset[0],
        state_offset[1] * 0.2 + gaze[1] + typing_offset[1],
    )


def pointer_near(px: float, py: float, cx: float, cy: float, radius: float = 240.0) -> bool:
    return math.hypot(px - cx, py - cy) <= radius


def tick_interval(
    *,
    visible: bool,
    state: str,
    phase: float,
    tracking: bool,
    pointer_near_eye: bool,
    mouse_moved: bool,
    typing: bool,
) -> float:
    """Seconds between redraws. Idle eyes tick slowly; motion and work do not."""
    if not visible:
        return 0.5
    if state in {"thinking", "waiting"}:
        return 1.0 / 30.0
    if state == "done":
        from .appearance import pulse_alpha

        if pulse_alpha("done", phase) > 0.0:
            return 1.0 / 30.0
    if tracking and (pointer_near_eye or mouse_moved or typing):
        return 1.0 / 30.0
    return 1.0 / 8.0


def hint_for_monitor(token: object) -> str:
    """A missing global key monitor means we keep mouse tracking and say why."""
    if token is None:
        return KEYBOARD_HINT
    return ""


def key_activity_handler(mark):
    """Global key callback. Marks activity and does not read the event."""

    def _on_key(_event) -> None:
        mark()

    return _on_key
