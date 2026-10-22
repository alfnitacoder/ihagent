"""Robot iheye skin: pose math and a draw list.

Glossy white head and body, a black glass face, cyan LEDs, ear rings, a
lit antenna, and a hovering energy ring. The overlay paints the list in
``drawRect_`` with Core Graphics. Nothing here imports AppKit, and nothing
here builds a CALayer tree.

``listening`` is a pose for previews only. ihagent has no microphone, so
``bot_mood`` never selects it. ``speaking`` is a streaming reply while the
agent state is still ``thinking``. ``working`` and ``failed`` are side
flags, not protocol states.

After the robot has been idle for a few seconds it plays a short flourish
from ``IDLE_ANIMATIONS`` (sway, hop, wave, spin, breathe), then the next.
Any real activity drops the flourish on the next pose.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from dataclasses import dataclass

from .appearance import normalize_state
from .tracking import PHASE_PER_SEC

BOT_W = 168.0
BOT_H = 220.0

# ``done`` stays a protocol state. The celebration is this many phase units
# (phase advances at 3.6 per second), then the robot sits in the idle pose.
SUCCESS_PHASE = 8.6

BOT_MOODS = ("idle", "thinking", "listening", "speaking", "working", "success", "error")

_ON = {"1", "true", "yes", "on"}

# Design-space layout. y grows upward. The ring stays put; the body floats.
_CX = 84.0
_HEAD = (84.0, 146.0, 46.0)  # cx, cy, r
_BODY = (84.0, 90.0, 32.0, 34.0)  # cx, cy, rx, ry
_RING = (84.0, 34.0, 46.0, 13.0)

_LED = {
    "idle": (0.22, 0.84, 1.00),
    "thinking": (0.32, 0.74, 1.00),
    "listening": (0.45, 0.92, 1.00),
    "speaking": (0.48, 0.90, 1.00),
    "working": (0.18, 0.86, 1.00),
    "success": (0.25, 0.90, 1.00),
    "error": (1.00, 0.58, 0.14),
}
_HOT = {
    "idle": (0.78, 0.97, 1.00),
    "thinking": (0.72, 0.90, 1.00),
    "listening": (0.82, 0.98, 1.00),
    "speaking": (0.85, 0.97, 1.00),
    "working": (0.75, 0.96, 1.00),
    "success": (0.80, 0.97, 1.00),
    "error": (1.00, 0.86, 0.48),
}
_WHITE = (0.97, 0.98, 1.00)
_SHADE = (0.50, 0.60, 0.74)
_CLASSIC_REACH = 6.0
_GAZE_X = 3.2
_GAZE_Y = 2.2

_RING_SPEED = {
    "idle": 0.28,
    "thinking": 1.05,
    "listening": 0.55,
    "speaking": 0.62,
    "working": 1.85,
    "success": 1.15,
    "error": 0.20,
}
_TILT = {
    "idle": 0.0,
    "thinking": -12.0,
    "listening": 4.0,
    "speaking": -3.0,
    "working": 2.5,
    "success": -4.0,
    "error": 8.0,
}


@dataclass(frozen=True)
class BotPose:
    """One frame. Distances are overlay points before the view scales them."""

    state: str
    mood: str
    bob: float
    tilt: float
    blink: float
    eye_open: float
    gaze_x: float
    gaze_y: float
    smile: float
    mouth_open: float
    ring: float
    ring_speed: float
    wave: float
    chest: float
    scan: float
    led_rgb: tuple[float, float, float]
    hot_rgb: tuple[float, float, float]
    glow: float
    reduce_motion: bool
    # Which flourish is playing, or "" while resting or busy.
    idle_anim: str = ""


def reduce_motion_from_env(environ: dict[str, str] | None = None) -> bool:
    """``HFAGENT_IHEYE_REDUCE_MOTION`` — ``1`` / ``true`` / ``yes`` / ``on``."""
    env = os.environ if environ is None else environ
    raw = env.get("HFAGENT_IHEYE_REDUCE_MOTION")
    if raw is None or str(raw).strip() == "":
        return False
    return str(raw).strip().lower() in _ON


def bot_mood(
    state: str,
    *,
    speaking: bool = False,
    working: bool = False,
    failed: bool = False,
) -> str:
    """Map an agent phase onto a robot pose.

    Listening is intentionally absent: nothing in the agent feeds a mic.
    ``working`` wins over a leftover reply. ``waiting`` and ``failed`` share
    the amber pose. ``done`` is the celebration; the pose itself drops back
    to idle after ``SUCCESS_PHASE``.
    """
    state = normalize_state(state)
    if failed or state == "waiting":
        return "error"
    if state == "done":
        return "success"
    if state == "thinking" and working:
        return "working"
    if state == "thinking" and speaking:
        return "speaking"
    if state == "thinking":
        return "thinking"
    return "idle"


def point_in_bot(x: float, y: float, width: float, height: float) -> bool:
    """Head, body, or the ring. Corners of the window fall through."""
    if width <= 0 or height <= 0:
        return False
    dx = x / float(width) * BOT_W
    dy = y / float(height) * BOT_H
    hx, hy, hr = _HEAD
    if math.hypot(dx - hx, dy - hy) <= hr + 6.0:
        return True
    bx, by, brx, bry = _BODY
    if ((dx - bx) / (brx + 8.0)) ** 2 + ((dy - by) / (bry + 8.0)) ** 2 <= 1.0:
        return True
    rx, ry, rrx, rry = _RING
    if ((dx - rx) / (rrx + 8.0)) ** 2 + ((dy - ry) / (rry + 8.0)) ** 2 <= 1.0:
        return True
    return False


def bot_pose(
    state: str,
    phase: float,
    *,
    pupil: tuple[float, float] | None = None,
    blink_extra: float = 0.0,
    speaking: bool = False,
    working: bool = False,
    failed: bool = False,
    reduce_motion: bool | None = None,
    mood: str | None = None,
    idle_for: float | None = None,
) -> BotPose:
    """Pose for one overlay tick.

    Pass ``mood`` only to force a preview frame (the listening sheet).
    Hooks leave it unset so ``bot_mood`` decides.
    """
    state = normalize_state(state)
    phase = float(phase)
    if reduce_motion is None:
        reduce_motion = reduce_motion_from_env()
    settled = bool(reduce_motion)
    if mood is None:
        visual = bot_mood(state, speaking=speaking, working=working, failed=failed)
        if visual == "success" and phase >= SUCCESS_PHASE:
            visual = "idle"
    else:
        visual = mood if mood in BOT_MOODS else "idle"
    blink = _blink_amount(visual, phase, blink_extra, settled)
    bob = 0.0 if settled else _bob(visual, phase)
    tilt = _TILT[visual]
    if not settled:
        tilt += math.sin(phase * 0.65) * (2.4 if visual == "thinking" else 0.8)
    gaze_x, gaze_y = _gaze(visual, phase, pupil, settled)
    eye_open = _eye_open(visual, blink, phase, settled)
    smile, mouth_open = _mouth(visual, phase, settled)
    speed = _RING_SPEED[visual]
    ring = 0.55 if settled else phase * speed
    wave = 0.0 if settled else _wave(visual, phase)
    chest = _chest(visual, phase, settled)
    scan = 0.0 if settled or visual != "thinking" else math.sin(phase * 2.5) * 3.4
    pulse = 0.5 + 0.5 * math.sin(phase * (0.9 if visual == "error" else 1.4))
    if settled:
        pulse = 0.65 if visual == "error" else 0.45
    glow = 0.55 + (0.40 if visual == "error" else 0.22) * pulse
    flourish = ""
    if not settled and visual == "idle":
        if idle_for is None:
            if state == "idle":
                idle_for = phase / PHASE_PER_SEC
            elif state == "done":
                idle_for = max(0.0, (phase - SUCCESS_PHASE) / PHASE_PER_SEC)
            else:
                idle_for = 0.0
        playing = idle_flourish(idle_for)
        if playing is not None:
            flourish, delta = playing
            bob += delta.bob
            tilt += delta.tilt
            wave = min(1.0, max(0.0, wave + delta.wave))
            chest = min(1.0, max(0.0, chest + delta.chest))
            ring += delta.ring
            # The antenna sits near the top of the window. Keep the hop inside it.
            bob = min(9.5, bob)
    return BotPose(
        state=state,
        mood=visual,
        bob=bob,
        tilt=tilt,
        blink=blink,
        eye_open=eye_open,
        gaze_x=gaze_x,
        gaze_y=gaze_y,
        smile=smile,
        mouth_open=mouth_open,
        ring=ring,
        ring_speed=speed,
        wave=wave,
        chest=chest,
        scan=scan,
        led_rgb=_LED[visual],
        hot_rgb=_HOT[visual],
        glow=glow,
        reduce_motion=settled,
        idle_anim=flourish,
    )


# Seconds of sitting still before the first flourish, then how long each
# one plays and how long the robot rests before the next. Add a flourish
# by appending ``(name, function)`` to ``IDLE_ANIMATIONS``. The function
# takes ``(t, duration)`` and returns an ``IdleDelta`` that is 0 at both ends.
IDLE_TIMEOUT = 5.0
IDLE_PLAY = 2.4
IDLE_GAP = 1.6


@dataclass(frozen=True)
class IdleDelta:
    """Added on top of the resting idle pose. Zero means "not this channel"."""

    bob: float = 0.0
    tilt: float = 0.0
    wave: float = 0.0
    chest: float = 0.0
    ring: float = 0.0


def _idle_unit(t: float, duration: float) -> float:
    if duration <= 0:
        return 0.0
    return max(0.0, min(1.0, float(t) / float(duration)))


def _idle_arch(t: float, duration: float) -> float:
    """One smooth hump. 0 at the start and the end, 1 in the middle."""
    return math.sin(math.pi * _idle_unit(t, duration))


def _idle_fade(t: float, duration: float) -> float:
    """1 through the middle, eased to 0 at both ends so a flourish can stop cleanly."""
    p = _idle_unit(t, duration)
    edge = 0.18
    if p < edge:
        u = p / edge
        return u * u * (3.0 - 2.0 * u)
    if p > 1.0 - edge:
        u = (1.0 - p) / edge
        return u * u * (3.0 - 2.0 * u)
    return 1.0


def _anim_sway(t: float, duration: float) -> IdleDelta:
    """Rock side to side, twice, with a small weight shift."""
    fade = _idle_fade(t, duration)
    rock = math.sin(4.0 * math.pi * _idle_unit(t, duration))
    shift = math.sin(2.0 * math.pi * _idle_unit(t, duration))
    return IdleDelta(tilt=rock * 6.0 * fade, bob=shift * 1.1 * fade)


def _anim_hop(t: float, duration: float) -> IdleDelta:
    """One small jump and a soft landing."""
    return IdleDelta(bob=_idle_arch(t, duration) * 8.0)


def _anim_wave(t: float, duration: float) -> IdleDelta:
    """Raise the free hand once, with a slight lean the other way."""
    arch = _idle_arch(t, duration)
    return IdleDelta(wave=arch * 0.9, tilt=arch * -3.0)


def _anim_spin(t: float, duration: float) -> IdleDelta:
    """A head tilt plus a partial turn of the energy ring. Not a full whirl."""
    fade = _idle_fade(t, duration)
    return IdleDelta(tilt=fade * 14.0, ring=fade * 1.15)


def _anim_breathe(t: float, duration: float) -> IdleDelta:
    """Two slow chest pulses and a tiny rise, quieter than the hop."""
    fade = _idle_fade(t, duration)
    unit = _idle_unit(t, duration)
    pulse = 0.5 - 0.5 * math.cos(4.0 * math.pi * unit)
    rise = math.sin(4.0 * math.pi * unit)
    return IdleDelta(chest=pulse * 0.42 * fade, bob=rise * 2.0 * fade)


# Order is the rotation. Extend this tuple; nothing else has to change.
IDLE_ANIMATIONS: tuple[tuple[str, Callable[[float, float], IdleDelta]], ...] = (
    ("sway", _anim_sway),
    ("hop", _anim_hop),
    ("wave", _anim_wave),
    ("spin", _anim_spin),
    ("breathe", _anim_breathe),
)
IDLE_NAMES = tuple(name for name, _fn in IDLE_ANIMATIONS)


def idle_flourish(idle_for: float) -> tuple[str, IdleDelta] | None:
    """The flourish for this many idle seconds, or None while busy waiting.

    Before ``IDLE_TIMEOUT`` the robot holds its ordinary idle pose. After
    that the five flourishes take turns. The gap between them is None, so
    the pose eases back to rest instead of snapping into the next one.
    """
    try:
        elapsed_idle = float(idle_for)
    except (TypeError, ValueError):
        return None
    if elapsed_idle != elapsed_idle or elapsed_idle < IDLE_TIMEOUT:
        return None
    if IDLE_PLAY <= 0 or not IDLE_ANIMATIONS:
        return None
    slot = IDLE_PLAY + IDLE_GAP
    elapsed = elapsed_idle - IDLE_TIMEOUT
    index = int(elapsed // slot) % len(IDLE_ANIMATIONS)
    local = elapsed - (int(elapsed // slot) * slot)
    if local < 0.0 or local >= IDLE_PLAY:
        return None
    name, fn = IDLE_ANIMATIONS[index]
    return name, fn(local, IDLE_PLAY)


def describe_bot(
    pose: BotPose,
    width: float = BOT_W,
    height: float = BOT_H,
) -> tuple[dict, ...]:
    """Draw ops shared by tests, the preview rasterizer, and ``drawRect_``."""
    ops: list[dict] = []
    bob = pose.bob
    led = pose.led_rgb
    hot = pose.hot_rgb
    hx, hy, hr = _HEAD
    hy += bob
    bx, by, brx, bry = _BODY
    by += bob
    tilt = pose.tilt

    def spin(x: float, y: float) -> tuple[float, float]:
        return _rotate(x, y, hx, hy, tilt)

    # Soft glow and a contact shadow. The ring does not bob.
    glow_a = 0.05 + 0.05 * min(1.0, pose.glow)
    ops.append(_oval(_CX, by, 78, 70, led, glow_a))
    ops.append(_oval(_CX, by - 6, 52, 40, led, glow_a * 1.3))
    shadow = max(0.05, 0.16 - bob * 0.008)
    ops.append(_oval(_CX, 26, 34 + bob * 0.15, 7.5, (0.02, 0.04, 0.08), shadow))

    rcx, rcy, rrx, rry = _RING
    ops.append(_oval(rcx, rcy, rrx + 10, rry + 7, led, 0.10 + 0.06 * pose.glow))
    ops.append(_oval(rcx, rcy, rrx - 4, rry - 2, led, 0.16))
    duty = 0.46
    dashes = 18 if pose.mood != "working" else 22
    ops.extend(_ring_dashes(rcx, rcy, rrx, rry, pose.ring, dashes, duty, led, 0.85, 2.6))
    ops.extend(
        _ring_dashes(rcx, rcy, rrx * 0.62, rry * 0.62, -pose.ring * 0.65, 12, 0.38, hot, 0.7, 1.6)
    )

    lax, lay = _arm(left=True, bob=bob, wave=0.0)
    rax, ray = _arm(left=False, bob=bob, wave=pose.wave)
    _paint_arm(ops, lax, lay)
    _paint_arm(ops, rax, ray)

    _shell(ops, bx, by, brx, bry)
    chest_y = by + 2.0
    ops.append(_oval(bx, chest_y, 6.2, 6.2, led, 0.18 + 0.75 * pose.chest))
    ops.append(_oval(bx, chest_y + 0.4, 2.6, 2.6, hot, 0.35 + 0.65 * pose.chest))

    _shell(ops, hx, hy, hr, hr)
    ops.append(_stroke(hx, hy, hr + 0.8, hr + 0.8, 1.15, (1.0, 1.0, 1.0), 0.28))

    for side in (-1.0, 1.0):
        ex, ey = spin(hx + side * (hr - 2.0), hy + 2.0)
        ops.append(_oval(ex, ey, 14, 16, led, 0.16 + 0.10 * pose.glow))
        ops.append(_stroke(ex, ey, 9.5, 12.0, 2.3, led, 0.9))
        ops.append(_stroke(ex, ey, 5.5, 7.2, 1.3, hot, 0.75))

    tip_x, tip_y = spin(hx, hy + hr + 16.0)
    base_x, base_y = spin(hx, hy + hr - 2.0)
    ops.append(_limb(base_x, base_y, tip_x, tip_y, 2.4, (0.72, 0.78, 0.86), 1.0))
    ops.append(_oval(tip_x, tip_y, 7.5, 7.5, led, 0.35))
    ops.append(_oval(tip_x, tip_y, 3.6, 3.6, hot, 1.0))

    face_c = spin(hx + pose.gaze_x * 0.15, hy - 2.0 + pose.gaze_y * 0.1)
    fw, fh = 60.0, 48.0
    ops.append(
        _round(face_c[0] - fw / 2, face_c[1] - fh / 2, fw, fh, 16.0, (0.03, 0.04, 0.06), 0.96)
    )
    ops.append(_oval(face_c[0] - 6, face_c[1] + 10, 18, 8, (1, 1, 1), 0.06))
    ops.append(_stroke(face_c[0], face_c[1], fw / 2 - 1, fh / 2 - 1, 1.2, led, 0.28))

    eye_rx = 8.2
    eye_ry = max(1.15, 3.5 * pose.eye_open)
    for side in (-1.0, 1.0):
        ex, ey = spin(hx + side * 14.5 + pose.gaze_x, hy + 6.5 + pose.gaze_y)
        ex += pose.scan * (0.35 if side < 0 else 1.0)
        _paint_eye(ops, ex, ey, eye_rx, eye_ry, led, hot)

    mx, my = spin(hx + pose.gaze_x * 0.25, hy - 16.0 + pose.gaze_y * 0.15)
    ops.extend(_mouth_ops(mx, my, pose.smile, pose.mouth_open, led, hot))
    return _scale_ops(ops, float(width), float(height))


def rasterize(ops: tuple[dict, ...] | list[dict], width: float, height: float, scale: int = 1):
    """Rasterize draw ops with Pillow. Imported lazily so the agent need not depend on it."""
    from PIL import Image, ImageChops, ImageDraw

    scale = max(1, int(scale))
    pixel_w = max(1, int(round(width * scale)))
    pixel_h = max(1, int(round(height * scale)))
    image = Image.new("RGBA", (pixel_w, pixel_h), (0, 0, 0, 0))

    def xy(x: float, y: float) -> tuple[float, float]:
        return x * scale, pixel_h - y * scale

    def rgba(rgb, alpha) -> tuple[int, int, int, int]:
        r, g, b = rgb
        return (
            int(max(0.0, min(1.0, r)) * 255),
            int(max(0.0, min(1.0, g)) * 255),
            int(max(0.0, min(1.0, b)) * 255),
            int(max(0.0, min(1.0, alpha)) * 255),
        )

    def bbox_oval(cx, cy, rx, ry):
        x0, y0 = xy(cx - rx, cy + ry)
        x1, y1 = xy(cx + rx, cy - ry)
        return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]

    for op in ops:
        layer = Image.new("RGBA", (pixel_w, pixel_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        kind = op.get("op")
        color = rgba(op.get("rgb") or (1, 1, 1), float(op.get("alpha", 1.0)))
        width_px = max(1, int(round(float(op.get("width") or 1.0) * scale)))
        if kind == "oval":
            draw.ellipse(bbox_oval(op["cx"], op["cy"], op["rx"], op["ry"]), fill=color)
        elif kind == "stroke":
            draw.ellipse(
                bbox_oval(op["cx"], op["cy"], op["rx"], op["ry"]),
                outline=color,
                width=width_px,
            )
        elif kind == "roundrect":
            x0, y0 = xy(op["x"], op["y"] + op["h"])
            x1, y1 = xy(op["x"] + op["w"], op["y"])
            radius = float(op.get("radius") or 0.0) * scale
            draw.rounded_rectangle(
                [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
                radius=radius,
                fill=color,
            )
        elif kind == "limb":
            draw.line([xy(op["x0"], op["y0"]), xy(op["x1"], op["y1"])], fill=color, width=width_px)
        elif kind == "poly":
            pts = [xy(x, y) for x, y in op.get("points") or []]
            if len(pts) >= 2:
                draw.line(pts, fill=color, width=width_px, joint="curve")
        clip = op.get("clip")
        if clip:
            mask = Image.new("L", (pixel_w, pixel_h), 0)
            mask_draw = ImageDraw.Draw(mask)
            if "r" in clip:
                mask_draw.ellipse(bbox_oval(clip["cx"], clip["cy"], clip["r"], clip["r"]), fill=255)
            else:
                mask_draw.ellipse(
                    bbox_oval(clip["cx"], clip["cy"], clip["rx"], clip["ry"]),
                    fill=255,
                )
            band = layer.getchannel("A")
            layer.putalpha(ImageChops.multiply(band, mask))
        image = Image.alpha_composite(image, layer)
    return image


def _blink_amount(mood: str, phase: float, extra: float, settled: bool) -> float:
    if settled or mood in {"success", "error", "listening"}:
        base = 0.0
    else:
        period = 11.0 if mood == "speaking" else 9.2
        width = 0.72
        pos = phase % period
        if 0.0 <= pos < width:
            base = math.sin(pos / width * math.pi)
        else:
            base = 0.0
    if settled:
        return 0.0
    return min(1.0, base + max(0.0, float(extra or 0.0)))


def _bob(mood: str, phase: float) -> float:
    if mood == "success":
        return abs(math.sin(phase * 2.3)) * 8.0
    amp = {
        "idle": 3.4,
        "thinking": 1.5,
        "listening": 1.8,
        "speaking": 1.7,
        "working": 1.2,
        "error": 0.7,
    }[mood]
    rate = 0.85 if mood == "error" else 0.55 if mood == "idle" else 0.7
    return math.sin(phase * rate) * amp


def _wave(mood: str, phase: float) -> float:
    if mood == "success":
        return max(0.0, math.sin(min(phase, math.pi)))
    if mood == "speaking":
        return 0.08 + 0.06 * math.sin(phase * 1.4)
    return math.sin(phase * 0.5) * 0.05


def _chest(mood: str, phase: float, settled: bool) -> float:
    if mood == "working":
        if settled:
            return 1.0
        return 0.82 + 0.18 * (0.5 + 0.5 * math.sin(phase * 3.2))
    return {
        "idle": 0.18,
        "thinking": 0.34,
        "listening": 0.28,
        "speaking": 0.30,
        "success": 0.62,
        "error": 0.40,
    }[mood]


def _gaze(
    mood: str,
    phase: float,
    pupil: tuple[float, float] | None,
    settled: bool,
) -> tuple[float, float]:
    if pupil is not None:
        gx = float(pupil[0]) / _CLASSIC_REACH * _GAZE_X
        gy = float(pupil[1]) / _CLASSIC_REACH * _GAZE_Y
    elif settled:
        gx, gy = 0.0, 0.0
    else:
        gx = math.sin(phase * 0.22) * 1.05
        gy = math.cos(phase * 0.18) * 0.45
    if mood == "thinking":
        gy += 1.7
    elif mood == "error":
        gy -= 0.6
    elif mood == "success":
        gy += 0.4
    return _clamp(gx, gy, _GAZE_X, _GAZE_Y)


def _eye_open(mood: str, blink: float, phase: float, settled: bool) -> float:
    base = {
        "idle": 1.0,
        "thinking": 0.58,
        "listening": 1.18,
        "speaking": 0.96,
        "working": 0.84,
        "success": 0.46,
        "error": 1.16,
    }[mood]
    if not settled and mood == "thinking":
        base *= 0.92 + 0.08 * math.sin(phase * 1.7)
    return max(0.12, base * (1.0 - 0.9 * blink))


def _mouth(mood: str, phase: float, settled: bool) -> tuple[float, float]:
    if mood == "speaking":
        if settled:
            return 0.35, 0.42
        a = abs(math.sin(phase * 2.15))
        b = abs(math.sin(phase * 5.4 + 0.6))
        return 0.28, 0.16 + 0.78 * (0.65 * a + 0.35 * b)
    if mood == "thinking":
        hmm = 0.0 if settled else max(0.0, math.sin(phase * 0.9))
        return 0.02, 0.04 + 0.08 * hmm
    if mood == "error":
        return -0.72, 0.05
    if mood == "success":
        return 0.95, 0.08
    if mood == "listening":
        return 0.2, 0.16
    if mood == "working":
        return 0.12, 0.05
    return 0.48, 0.04


def _arm(left: bool, bob: float, wave: float) -> tuple[tuple[float, float], tuple[float, float]]:
    if left:
        shoulder = (56.0, 104.0 + bob)
        rest = (40.0, 80.0 + bob)
        raised = (36.0, 92.0 + bob)
    else:
        shoulder = (112.0, 104.0 + bob)
        rest = (128.0, 80.0 + bob)
        raised = (126.0, 148.0 + bob)
    hand = (
        rest[0] + (raised[0] - rest[0]) * wave,
        rest[1] + (raised[1] - rest[1]) * wave,
    )
    return shoulder, hand


def _paint_arm(ops: list[dict], shoulder: tuple[float, float], hand: tuple[float, float]) -> None:
    x0, y0 = shoulder
    x1, y1 = hand
    ops.append(_limb(x0, y0, x1, y1, 13.0, _SHADE, 1.0))
    ops.append(_limb(x0, y0, x1, y1, 9.5, _WHITE, 1.0))
    ops.append(_oval(x1, y1, 7.2, 7.2, (0.93, 0.95, 0.98), 1.0))
    ops.append(_oval(x1 - 1.4, y1 + 1.6, 2.4, 1.6, (1, 1, 1), 0.65))


def _paint_eye(ops, cx, cy, rx, ry, led, hot) -> None:
    ops.append(_oval(cx, cy, rx * 1.9, max(2.2, ry * 2.4), led, 0.22))
    ops.append(_oval(cx, cy, rx, max(1.05, ry), led, 1.0))
    ops.append(_oval(cx, cy + ry * 0.15, rx * 0.42, max(0.6, ry * 0.45), hot, 0.95))


def _mouth_ops(cx, cy, smile, opened, led, hot) -> list[dict]:
    if opened >= 0.2:
        rx = 7.5 + 3.0 * (1.0 - min(1.0, opened))
        ry = 2.0 + 6.5 * opened
        return [
            _oval(cx, cy, rx * 1.35, ry * 1.35, led, 0.22),
            _oval(cx, cy, rx, max(1.4, ry), led, 1.0),
            _oval(cx, cy + 0.6, rx * 0.4, max(0.7, ry * 0.4), hot, 0.85),
        ]
    if smile >= 0.12:
        sweep = 26.0 + 28.0 * min(1.0, smile)
        radius = 16.5 - 2.0 * min(1.0, smile)
        center_y = cy + radius * 0.08
        pts = _arc_points(cx, center_y, radius, 270.0 - sweep, 270.0 + sweep)
    elif smile <= -0.12:
        sweep = 22.0 + 16.0 * min(1.0, -smile)
        radius = 15.0
        center_y = cy - radius * 0.05
        pts = _arc_points(cx, center_y, radius, 90.0 - sweep, 90.0 + sweep)
    else:
        pts = [(cx - 8.0, cy), (cx + 8.0, cy)]
    return [
        {"op": "poly", "points": pts, "width": 4.4, "rgb": led, "alpha": 0.95},
        {"op": "poly", "points": pts, "width": 1.8, "rgb": hot, "alpha": 0.9},
    ]


def _shell(ops: list[dict], cx: float, cy: float, rx: float, ry: float) -> None:
    clip = {"cx": cx, "cy": cy, "rx": rx, "ry": ry} if rx != ry else {"cx": cx, "cy": cy, "r": rx}
    ops.append(_oval(cx, cy, rx, ry, (0.84, 0.89, 0.95), 1.0, clip))
    ops.append(_oval(cx, cy + ry * 0.06, rx * 0.98, ry * 0.98, _WHITE, 1.0, clip))
    ops.append(_oval(cx + rx * 0.08, cy - ry * 0.34, rx * 0.88, ry * 0.52, _SHADE, 0.40, clip))
    ops.append(_oval(cx - rx * 0.32, cy - ry * 0.02, rx * 0.48, ry * 0.72, (0.62, 0.72, 0.84), 0.16, clip))
    ops.append(_oval(cx - rx * 0.18, cy + ry * 0.42, rx * 0.46, ry * 0.28, (1, 1, 1), 0.55, clip))
    ops.append(_oval(cx - rx * 0.28, cy + ry * 0.52, rx * 0.14, ry * 0.09, (1, 1, 1), 0.92, clip))


def _ring_dashes(cx, cy, rx, ry, rotation, dashes, duty, rgb, alpha, width) -> list[dict]:
    ops = []
    span = math.tau / max(1, int(dashes))
    for index in range(int(dashes)):
        a0 = rotation + index * span
        a1 = a0 + span * duty
        pts = []
        steps = 4
        for step in range(steps + 1):
            angle = a0 + (a1 - a0) * step / steps
            pts.append((cx + math.cos(angle) * rx, cy + math.sin(angle) * ry))
        ops.append({"op": "poly", "points": pts, "width": width, "rgb": rgb, "alpha": alpha})
    return ops


def _arc_points(cx, cy, radius, start_deg, end_deg, steps: int = 12) -> list[tuple[float, float]]:
    pts = []
    for step in range(steps + 1):
        angle = math.radians(start_deg + (end_deg - start_deg) * step / steps)
        pts.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    return pts


def _rotate(x: float, y: float, cx: float, cy: float, degrees: float) -> tuple[float, float]:
    if degrees == 0.0:
        return x, y
    angle = math.radians(degrees)
    dx, dy = x - cx, y - cy
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    return cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a


def _clamp(dx: float, dy: float, limit_x: float, limit_y: float) -> tuple[float, float]:
    if limit_x <= 0.0 or limit_y <= 0.0:
        return 0.0, 0.0
    dist = math.hypot(dx / limit_x, dy / limit_y)
    if dist <= 1.0 or dist == 0.0:
        return dx, dy
    return dx / dist, dy / dist


def _oval(cx, cy, rx, ry, rgb, alpha, clip=None) -> dict:
    op = {"op": "oval", "cx": cx, "cy": cy, "rx": rx, "ry": ry, "rgb": rgb, "alpha": alpha}
    if clip is not None:
        op["clip"] = clip
    return op


def _stroke(cx, cy, rx, ry, width, rgb, alpha) -> dict:
    return {
        "op": "stroke",
        "cx": cx,
        "cy": cy,
        "rx": rx,
        "ry": ry,
        "width": width,
        "rgb": rgb,
        "alpha": alpha,
    }


def _round(x, y, w, h, radius, rgb, alpha) -> dict:
    return {
        "op": "roundrect",
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "radius": radius,
        "rgb": rgb,
        "alpha": alpha,
    }


def _limb(x0, y0, x1, y1, width, rgb, alpha) -> dict:
    return {
        "op": "limb",
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "width": width,
        "rgb": rgb,
        "alpha": alpha,
    }


def _scale_ops(ops: list[dict], width: float, height: float) -> tuple[dict, ...]:
    sx = width / BOT_W if BOT_W else 1.0
    sy = height / BOT_H if BOT_H else 1.0
    sw = (sx + sy) * 0.5
    scaled = []
    for op in ops:
        item = dict(op)
        for key in ("cx", "x0", "x1", "x"):
            if key in item:
                item[key] = float(item[key]) * sx
        for key in ("cy", "y0", "y1", "y"):
            if key in item:
                item[key] = float(item[key]) * sy
        if "rx" in item:
            item["rx"] = float(item["rx"]) * sx
        if "ry" in item:
            item["ry"] = float(item["ry"]) * sy
        if "w" in item:
            item["w"] = float(item["w"]) * sx
        if "h" in item:
            item["h"] = float(item["h"]) * sy
        if "radius" in item:
            item["radius"] = float(item["radius"]) * min(sx, sy)
        if "width" in item and item.get("op") != "roundrect":
            item["width"] = float(item["width"]) * sw
        if "points" in item:
            item["points"] = [(float(x) * sx, float(y) * sy) for x, y in item["points"]]
        clip = item.get("clip")
        if clip:
            mapped = {"cx": float(clip["cx"]) * sx, "cy": float(clip["cy"]) * sy}
            if "r" in clip:
                mapped["r"] = float(clip["r"]) * min(sx, sy)
            else:
                mapped["rx"] = float(clip["rx"]) * sx
                mapped["ry"] = float(clip["ry"]) * sy
            item["clip"] = mapped
        scaled.append(item)
    return tuple(scaled)
