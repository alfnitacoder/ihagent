"""How the iheye bubble looks for each agent state.

Pure numbers so the drawing code and the tests share one description.
The overlay paints this; nothing here touches AppKit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


STATES = ("idle", "thinking", "waiting", "done")

# iris / glow RGB in 0..1
_IRIS = {
    "idle": ((0.18, 0.72, 0.58), (0.20, 0.86, 0.70)),
    "thinking": ((0.25, 0.72, 1.00), (0.35, 0.80, 1.00)),
    "waiting": ((1.00, 0.62, 0.12), (1.00, 0.78, 0.25)),
    "done": ((0.22, 0.82, 0.42), (0.45, 0.95, 0.55)),
}


@dataclass(frozen=True)
class Motion:
    pupil: tuple[float, float]
    blink: float
    pulse: float


@dataclass(frozen=True)
class EyeGeometry:
    """Bubble-local coordinates. y grows upward, matching AppKit."""

    left: float
    right: float
    top: float
    bottom: float
    iris: tuple[float, float, float]
    pupil: tuple[float, float, float]
    highlight: tuple[float, float, float]
    pulse: float
    iris_rgb: tuple[float, float, float]
    glow_rgb: tuple[float, float, float]
    state: str


def normalize_state(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in _IRIS:
        return text
    return "idle"


def pulse_alpha(state: str, phase: float) -> float:
    """0..1 strength of the attention ring. Waiting and done pulse hardest."""
    state = normalize_state(state)
    if state == "waiting":
        wave = 0.5 + 0.5 * math.sin(phase * 3.0)
        return 0.45 + 0.55 * wave
    if state == "done":
        # Two beats, then the ring rests. The iris stays green.
        if phase > math.pi * 4:
            return 0.0
        return 0.30 + 0.70 * abs(math.sin(phase * 2.5))
    if state == "thinking":
        wave = 0.5 + 0.5 * math.sin(phase * 1.2)
        return 0.10 + 0.15 * wave
    return 0.06


def blink_amount(state: str, phase: float) -> float:
    """0 open, 1 closed. Approval and the done flash keep the eye open."""
    state = normalize_state(state)
    if state in {"waiting", "done"}:
        return 0.0
    period = 8.0
    width = 0.35
    pos = phase % period
    if pos < width:
        return math.sin(pos / width * math.pi)
    return 0.0


def pupil_offset(state: str, phase: float) -> tuple[float, float]:
    state = normalize_state(state)
    if state == "thinking":
        return (math.sin(phase * 1.6) * 5.5, math.cos(phase * 0.8) * 1.6)
    if state == "waiting":
        return (math.sin(phase * 6.0) * 0.8, 0.0)
    if state == "done":
        return (0.0, 0.0)
    return (math.sin(phase * 0.35) * 1.0, 0.0)


def frame_motion(state: str, phase: float) -> Motion:
    return Motion(
        pupil=pupil_offset(state, phase),
        blink=blink_amount(state, phase),
        pulse=pulse_alpha(state, phase),
    )


def _clamp_pupil(cx: float, cy: float, dx: float, dy: float, limit: float) -> tuple[float, float]:
    dist = math.hypot(dx, dy)
    if dist <= limit or dist == 0:
        return cx + dx, cy + dy
    scale = limit / dist
    return cx + dx * scale, cy + dy * scale


def eye_geometry(state: str, phase: float, cx: float = 44.0, cy: float = 58.0) -> EyeGeometry:
    """One frame of the branded eye, ready to stroke with a bezier path."""
    state = normalize_state(state)
    motion = frame_motion(state, phase)
    iris_rgb, glow_rgb = _IRIS[state]
    eye_w = 46.0
    eye_h = 26.0 * (1.0 - 0.92 * motion.blink)
    eye_h = max(eye_h, 2.0)
    iris_r = max(2.4, 9.0 * (1.0 - 0.85 * motion.blink))
    pupil_r = max(1.2, iris_r * 0.48)
    px, py = _clamp_pupil(cx, cy, motion.pupil[0], motion.pupil[1], iris_r * 0.45)
    return EyeGeometry(
        left=cx - eye_w / 2,
        right=cx + eye_w / 2,
        top=cy + eye_h / 2,
        bottom=cy - eye_h / 2,
        iris=(cx, cy, iris_r),
        pupil=(px, py, pupil_r),
        highlight=(px - pupil_r * 0.35, py + pupil_r * 0.35, max(1.0, pupil_r * 0.28)),
        pulse=motion.pulse,
        iris_rgb=iris_rgb,
        glow_rgb=glow_rgb,
        state=state,
    )
