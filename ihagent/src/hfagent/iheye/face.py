"""Grok-style iheye face: pose math and a draw list.

Original geometric art: a dark disc, two white eyes, and a mouth. The
overlay paints the list in ``drawRect_`` with Core Graphics. Nothing here
imports AppKit, and nothing here builds a CALayer tree.

``speaking`` is not its own agent state. The helper already streams reply
text while ``state`` is ``thinking``; that combination is the talking face.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .appearance import normalize_state, pulse_alpha, pupil_offset

# Square overlay. The disc sits inside so a state rim can breathe past it.
FACE_W = 132.0
FACE_H = 132.0

# Classic pupil offsets top out near this many points. Map them onto the face.
_CLASSIC_REACH = 6.0
_TRAVEL_X = 7.0
_TRAVEL_Y = 4.6

_DISC = {
    "idle": (0.055, 0.058, 0.068),
    "thinking": (0.06, 0.085, 0.125),
    "speaking": (0.065, 0.078, 0.110),
    "waiting": (0.12, 0.075, 0.045),
    "done": (0.055, 0.105, 0.075),
}
_RIM = {
    "idle": (0.72, 0.80, 0.92),
    "thinking": (0.45, 0.74, 1.00),
    "speaking": (0.82, 0.90, 1.00),
    "waiting": (1.00, 0.62, 0.16),
    "done": (0.28, 0.95, 0.48),
}
_WHITE = (0.96, 0.97, 0.99)
_PUPIL = (0.05, 0.06, 0.08)


@dataclass(frozen=True)
class FacePose:
    """One frame of the face. Distances are overlay points, y grows upward."""

    state: str
    mood: str
    gaze_x: float
    gaze_y: float
    blink: float
    eye_open: float
    smile: float
    mouth_open: float
    scale: float
    brow: float
    disc_rgb: tuple[float, float, float]
    rim_rgb: tuple[float, float, float]
    rim_alpha: float


def face_mood(state: str, speaking: bool = False) -> str:
    """``thinking`` plus a streaming reply is the talking face."""
    state = normalize_state(state)
    if state == "thinking" and speaking:
        return "speaking"
    return state


def point_in_face(x: float, y: float, width: float, height: float) -> bool:
    """Hit test for the disc. Corners of the square window fall through."""
    if width <= 0 or height <= 0:
        return False
    radius = min(width, height) * 0.455
    dx = x - width / 2.0
    dy = y - height / 2.0
    return dx * dx + dy * dy <= radius * radius


def face_pose(
    state: str,
    phase: float,
    *,
    pupil: tuple[float, float] | None = None,
    blink_extra: float = 0.0,
    speaking: bool = False,
) -> FacePose:
    """Pose for one overlay tick.

    ``pupil`` is the classic-scale offset from tracking (or None, in which
    case the state's own glance is used). ``blink_extra`` is the keyboard
    glance blink, added on top of the idle blink.
    """
    state = normalize_state(state)
    mood = face_mood(state, speaking)
    blink = _blink(mood, phase, blink_extra)
    gaze_x, gaze_y = _gaze(state, mood, phase, pupil)
    eye_open = _eye_open(mood, blink, phase)
    smile, mouth_open = _mouth(mood, phase)
    scale = _breath(mood, phase)
    brow = math.sin(phase * 0.65) * (1.5 if mood == "thinking" else 0.22)
    pulse = pulse_alpha(state, phase)
    rim_alpha = _rim_alpha(mood, phase, pulse, mouth_open)
    return FacePose(
        state=state,
        mood=mood,
        gaze_x=gaze_x,
        gaze_y=gaze_y,
        blink=blink,
        eye_open=eye_open,
        smile=smile,
        mouth_open=mouth_open,
        scale=scale,
        brow=brow,
        disc_rgb=_DISC[mood],
        rim_rgb=_RIM[mood],
        rim_alpha=rim_alpha,
    )


def describe_face(
    pose: FacePose,
    width: float = FACE_W,
    height: float = FACE_H,
) -> tuple[dict, ...]:
    """Draw ops shared by the tests and the AppKit painter.

    Coordinates are AppKit points inside a ``width`` by ``height`` view.
    """
    cx = float(width) / 2.0
    cy = float(height) / 2.0
    scale = pose.scale
    radius = min(width, height) * 0.424 * scale

    def place(x: float, y: float) -> tuple[float, float]:
        return cx + x * scale, cy + y * scale

    ops: list[dict] = [
        {"op": "disc", "cx": cx, "cy": cy, "r": radius, "rgb": pose.disc_rgb, "alpha": 1.0},
        {
            "op": "shade",
            "cx": cx,
            "cy": cy - radius * 0.22,
            "rx": radius * 0.72,
            "ry": radius * 0.48,
            "clip_r": radius,
            "clip": (cx, cy),
            "rgb": (0.0, 0.0, 0.0),
            "alpha": 0.22,
        },
        {
            "op": "sheen",
            "cx": cx,
            "cy": cy + radius * 0.34,
            "rx": radius * 0.46,
            "ry": radius * 0.22,
            "clip_r": radius,
            "clip": (cx, cy),
            "rgb": (1.0, 1.0, 1.0),
            "alpha": 0.07,
        },
    ]

    eye_rx = 14.4 * scale
    eye_ry = max(1.6, 9.0 * pose.eye_open * scale)
    centers = (
        place(-17.5 + pose.gaze_x * 0.72, 11.5 + pose.gaze_y * 0.72 + pose.brow),
        place(17.5 + pose.gaze_x * 0.72, 11.5 + pose.gaze_y * 0.72 - pose.brow * 0.35),
    )
    # Looking sideways squashes the far eye a little so the gaze reads.
    side = max(-1.0, min(1.0, pose.gaze_x / _TRAVEL_X))
    spreads = (1.0 - 0.10 * side, 1.0 + 0.10 * side)
    for (ex, ey), spread in zip(centers, spreads):
        rx = eye_rx * spread
        ops.append({"op": "eye", "cx": ex, "cy": ey, "rx": rx, "ry": eye_ry, "rgb": _WHITE, "alpha": 1.0})
        pupil = _pupil_at(ex, ey, rx, eye_ry, pose.gaze_x, pose.gaze_y)
        if pupil is None:
            continue
        px, py, pr = pupil
        ops.append({"op": "pupil", "cx": px, "cy": py, "r": pr, "rgb": _PUPIL, "alpha": 1.0})
        ops.append(
            {
                "op": "catch",
                "cx": px - pr * 0.38,
                "cy": py + pr * 0.38,
                "r": max(0.7, pr * 0.28),
                "rgb": (1.0, 1.0, 1.0),
                "alpha": 0.9,
            }
        )

    mouth = _mouth_ops(pose, place, scale)
    ops.extend(mouth)
    if pose.rim_alpha > 0.02:
        ops.append(
            {
                "op": "rim",
                "cx": cx,
                "cy": cy,
                "r": radius + 1.6,
                "width": 2.4,
                "rgb": pose.rim_rgb,
                "alpha": pose.rim_alpha,
            }
        )
    return tuple(ops)


def _blink(mood: str, phase: float, extra: float) -> float:
    """0 open, 1 closed. Wide enough to read at the idle tick rate."""
    if mood in {"waiting", "done"}:
        base = 0.0
    else:
        period = 14.0 if mood == "speaking" else 9.5
        width = 0.85
        pos = phase % period
        if 0.0 <= pos < width:
            base = math.sin(pos / width * math.pi)
        else:
            base = 0.0
    return min(1.0, base + max(0.0, float(extra or 0.0)))


def _gaze(
    state: str,
    mood: str,
    phase: float,
    pupil: tuple[float, float] | None,
) -> tuple[float, float]:
    if pupil is None and mood == "speaking":
        gx = math.sin(phase * 0.35) * 1.2
        gy = math.cos(phase * 0.25) * 0.45
    elif pupil is None:
        gx, gy = pupil_offset(state, phase)
    else:
        gx, gy = float(pupil[0]), float(pupil[1])
    gx = gx / _CLASSIC_REACH * _TRAVEL_X
    gy = gy / _CLASSIC_REACH * _TRAVEL_Y
    if mood == "thinking":
        gy += 2.6
        gx += math.sin(phase * 0.45) * 1.15
    elif mood == "waiting":
        gy += math.sin(phase * 5.0) * 0.25
    elif mood == "done":
        gy += 0.35
    if pupil is None and mood == "idle":
        gx += math.sin(phase * 0.22) * 1.05
        gy += math.cos(phase * 0.17) * 0.55
    gy += math.sin(phase * 0.8) * 0.28
    return _clamp_ellipse(gx, gy, _TRAVEL_X, _TRAVEL_Y)


def _eye_open(mood: str, blink: float, phase: float) -> float:
    open_amount = 1.0 - 0.92 * blink
    if mood == "thinking":
        open_amount *= 0.58 + 0.04 * math.sin(phase * 0.7)
    elif mood == "done":
        open_amount *= 0.50
    elif mood == "waiting":
        open_amount = min(1.25, open_amount * 1.18)
    elif mood == "speaking":
        open_amount *= 0.94
    else:
        open_amount *= 0.94 + 0.06 * (0.5 + 0.5 * math.sin(phase * 0.5))
    return max(0.08, open_amount)


def _mouth(mood: str, phase: float) -> tuple[float, float]:
    if mood == "speaking":
        opened = _talk_open(phase)
        return 0.22, opened
    if mood == "thinking":
        hmm = max(0.0, math.sin(phase * 0.85))
        return 0.06, 0.04 + 0.16 * hmm
    if mood == "waiting":
        return -0.05, 0.46
    if mood == "done":
        return 0.92, 0.06
    return 0.42, 0.02


def _talk_open(phase: float) -> float:
    """Two uneven sines so the mouth does not loop like a metronome."""
    a = abs(math.sin(phase * 2.15))
    b = abs(math.sin(phase * 5.4 + 0.6))
    return 0.16 + 0.78 * (0.65 * a + 0.35 * b)


def _breath(mood: str, phase: float) -> float:
    rate = 1.15 if mood in {"thinking", "speaking"} else 0.62
    wave = 0.5 + 0.5 * math.sin(phase * rate)
    if mood == "speaking":
        return 0.99 + 0.02 * wave
    return 0.984 + 0.028 * wave


def _rim_alpha(mood: str, phase: float, pulse: float, mouth_open: float) -> float:
    if mood == "waiting":
        return 0.28 + 0.62 * pulse
    if mood == "done":
        return pulse
    if mood == "thinking":
        return 0.34 + 0.16 * (0.5 + 0.5 * math.sin(phase * 1.3))
    if mood == "speaking":
        return 0.22 + 0.28 * mouth_open
    return 0.08


def _mouth_ops(pose: FacePose, place, scale: float) -> list[dict]:
    mx, my = place(pose.gaze_x * 0.12, -16.0)
    if pose.mouth_open >= 0.18:
        rx = (8.5 + 4.0 * (1.0 - pose.mouth_open)) * scale
        ry = (2.0 + 9.0 * pose.mouth_open) * scale
        return [
            {
                "op": "mouth_blob",
                "cx": mx,
                "cy": my,
                "rx": rx,
                "ry": max(1.6, ry),
                "rgb": _WHITE,
                "alpha": 1.0,
            }
        ]
    if pose.smile >= 0.15:
        sweep = 46.0 + 36.0 * min(1.0, pose.smile)
        radius = (16.5 - 2.2 * pose.smile) * scale
        # Circle center sits above the mouth so the arc bows downward (a smile).
        center_y = my + radius * 0.42
        return [
            {
                "op": "mouth_arc",
                "cx": mx,
                "cy": center_y,
                "radius": radius,
                "start": 270.0 - sweep,
                "end": 270.0 + sweep,
                "width": 4.2 * scale,
                "rgb": _WHITE,
                "alpha": 1.0,
            }
        ]
    # Nearly straight, slightly concerned if smile is negative.
    half = 8.0 * scale
    droop = -pose.smile * 3.0 * scale
    return [
        {
            "op": "mouth_line",
            "x0": mx - half,
            "y0": my + droop,
            "x1": mx,
            "y1": my,
            "x2": mx + half,
            "y2": my + droop,
            "width": 3.6 * scale,
            "rgb": _WHITE,
            "alpha": 1.0,
        }
    ]


def _pupil_at(
    eye_cx: float,
    eye_cy: float,
    rx: float,
    ry: float,
    gaze_x: float,
    gaze_y: float,
) -> tuple[float, float, float] | None:
    pr = min(3.3, max(1.15, ry * 0.38))
    if ry < pr + 1.15:
        return None
    room_x = max(0.4, rx - pr - 1.3)
    room_y = max(0.4, ry - pr - 0.7)
    dx, dy = _clamp_ellipse(gaze_x * 0.55, gaze_y * 0.42, room_x, room_y)
    return eye_cx + dx, eye_cy + dy, pr


def _clamp_ellipse(dx: float, dy: float, limit_x: float, limit_y: float) -> tuple[float, float]:
    if limit_x <= 0.0 or limit_y <= 0.0:
        return 0.0, 0.0
    dist = math.hypot(dx / limit_x, dy / limit_y)
    if dist <= 1.0 or dist == 0.0:
        return dx, dy
    return dx / dist, dy / dist
