"""Where the on-screen ihmouse is, without drawing it.

The approval prompt blocks the agent thread. This state is ticked by the
overlay process, so the robot can glide and the highlight can stay up
while that prompt is showing. Nothing here reads the screen.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from .geometry import Rect


IDLE_SECONDS = 3.0
FADE_SECONDS = 0.7
PULSE_SECONDS = 0.45
_ON = {"1", "true", "yes", "on"}

_VERBS = {
    "click": "Click",
    "double_click": "Double-click",
    "right_click": "Right-click",
    "move": "Move",
    "scroll": "Scroll",
    "type": "Type",
    "key": "Key",
}


def reduce_motion_enabled(environ: dict[str, str] | None = None) -> bool:
    """Same switch as the iheye bot: ``HFAGENT_IHEYE_REDUCE_MOTION``."""
    env = os.environ if environ is None else environ
    return str(env.get("HFAGENT_IHEYE_REDUCE_MOTION", "")).strip().lower() in _ON


def smoothstep(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def glide_seconds(distance: float, reduce_motion: bool) -> float:
    """Short hops stay quick. A cross-screen glide caps at 0.7s."""
    if reduce_motion or distance < 3.0:
        return 0.0
    return min(0.7, max(0.18, distance / 1800.0))


def clamp_overlay_scale(value: object) -> float:
    """Cursor size. ``1`` is the designed arrow. Clamped to a visible range."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    if number != number:
        return 1.0
    return min(2.5, max(0.6, number))


def overlay_scale(environ: dict[str, str] | None = None) -> float:
    """``HFAGENT_MOUSE_OVERLAY_SCALE``. Unset means ``1``."""
    env = os.environ if environ is None else environ
    return clamp_overlay_scale(env.get("HFAGENT_MOUSE_OVERLAY_SCALE", "1") or "1")


def overlay_labels(environ: dict[str, str] | None = None) -> bool:
    """Action labels (``Click: Save``). ``HFAGENT_MOUSE_OVERLAY_LABEL`` off hides them.

    The IH badge drawn on the cursor is not this label.
    """
    env = os.environ if environ is None else environ
    raw = str(env.get("HFAGENT_MOUSE_OVERLAY_LABEL", "")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def effect_for(action: str) -> str:
    """Left click and keys pulse. Right and double click are their own ripples.

    Typing shows dots. Move and scroll only highlight; the glide itself
    leaves a trail.
    """
    if action == "double_click":
        return "double"
    if action == "right_click":
        return "right"
    if action in {"click", "key"}:
        return "pulse"
    if action == "type":
        return "typing"
    return ""


def _short(text: str, limit: int = 32) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


@dataclass(frozen=True)
class Cue:
    action: str
    x: float
    y: float
    label: str
    bounds: Rect | None = None


@dataclass(frozen=True)
class Frame:
    x: float
    y: float
    opacity: float
    visible: bool
    label: str
    bounds: Rect | None
    effect: str
    effect_p: float
    phase: float
    holding: bool
    arrived: bool
    trail: tuple[tuple[float, float], ...] = ()
    scale: float = 1.0
    show_label: bool = True


def make_cue(
    action: str,
    x: float,
    y: float,
    name: str = "",
    bounds: Rect | None = None,
    text: str = "",
    key: str = "",
) -> Cue:
    """``Click: Save`` from the element's name. Typing uses the text instead."""
    verb = _VERBS.get(action, action.replace("_", " ").title() or "Mouse")
    if action == "type" and text.strip():
        label = f"Type: {_short(text, 24)}"
    elif action == "key" and key.strip():
        label = f"Key: {_short(key, 24)}"
    elif name.strip():
        label = f"{verb}: {_short(name)}"
    else:
        label = verb
    if action == "show":
        return Cue("show", float(x), float(y), "", None)
    if bounds is None:
        bounds = Rect(float(x) - 16.0, float(y) - 16.0, 32.0, 32.0)
    return Cue(action, float(x), float(y), label, bounds)


def cue_from_message(message: dict) -> Cue:
    action = str(message.get("action") or "")
    raw = message.get("bounds")
    bounds = None
    if action != "show" and message.get("highlight") is not False:
        if isinstance(raw, (list, tuple)) and len(raw) == 4:
            bounds = Rect(float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    label = "" if action == "show" else str(message.get("label") or "")
    return Cue(
        action,
        float(message.get("x") or 0),
        float(message.get("y") or 0),
        label,
        bounds,
    )


class CursorMotion:
    """Glide, hold during approval, pulse, then fade after a few idle seconds."""

    def __init__(self, reduce_motion: bool | None = None) -> None:
        self.reduce_motion = reduce_motion_enabled() if reduce_motion is None else bool(reduce_motion)
        self.x = 0.0
        self.y = 0.0
        self.glide_duration = 0.0
        self._sx = 0.0
        self._sy = 0.0
        self._tx = 0.0
        self._ty = 0.0
        self._glide = 1.0
        self.label = ""
        self.bounds: Rect | None = None
        self.effect = ""
        self.effect_age = 0.0
        self.idle = 0.0
        self.holding = False
        self.visible = False
        self.suspended = False
        self.sticky = False
        self.phase = 0.0
        self.scale = overlay_scale()
        self.show_label = overlay_labels()
        self.trail: list[tuple[float, float]] = []
        self._trail_left = 0.0

    @property
    def arrived(self) -> bool:
        return self._glide >= 1.0

    def set_style(self, scale: float | None = None, labels: bool | None = None) -> None:
        if scale is not None:
            self.scale = clamp_overlay_scale(scale)
        if labels is not None:
            self.show_label = bool(labels)

    def _remember(self, x: float, y: float) -> None:
        point = (float(x), float(y))
        if self.trail:
            last = self.trail[-1]
            if math.hypot(point[0] - last[0], point[1] - last[1]) < 6.0:
                return
        self.trail.append(point)
        if len(self.trail) > 28:
            del self.trail[:-28]

    def propose(self, cue: Cue, origin: tuple[float, float] | None = None) -> None:
        was_visible = self.visible
        if self.visible:
            self._sx, self._sy = self.x, self.y
        elif origin is not None:
            self._sx, self._sy = float(origin[0]), float(origin[1])
            self.x, self.y = self._sx, self._sy
        else:
            self._sx, self._sy = self.x, self.y
        if not was_visible:
            self.trail.clear()
        self._remember(self._sx, self._sy)
        self._tx, self._ty = float(cue.x), float(cue.y)
        distance = math.hypot(self._tx - self._sx, self._ty - self._sy)
        self.glide_duration = glide_seconds(distance, self.reduce_motion)
        if self.glide_duration <= 0.0:
            self._glide = 1.0
            self.x, self.y = self._tx, self._ty
        else:
            self._glide = 0.0
        self.label = cue.label
        self.bounds = cue.bounds
        self.holding = True
        self.visible = True
        self.idle = 0.0
        self.effect = ""
        self.effect_age = 0.0

    def act(self, effect: str) -> None:
        self.effect = effect or ""
        self.effect_age = 0.0
        self.holding = True
        self.visible = True
        self.idle = 0.0

    def release(self) -> None:
        """The action finished. Fade starts after ``IDLE_SECONDS``."""
        self.holding = False
        self.idle = 0.0

    def hide(self) -> None:
        self.visible = False
        self.holding = False
        self.suspended = False
        self.sticky = False
        self.label = ""
        self.bounds = None
        self.effect = ""
        self.trail.clear()
        self._trail_left = 0.0

    def suspend(self) -> None:
        """Hide for a screenshot. The target is remembered."""
        self.suspended = True

    def resume(self) -> None:
        self.suspended = False

    def tick(self, dt: float) -> Frame:
        dt = max(0.0, float(dt))
        self.phase += dt
        moving = self._glide < 1.0 and self.glide_duration > 0.0
        if moving:
            self._glide = min(1.0, self._glide + dt / self.glide_duration)
            blended = smoothstep(self._glide)
            self.x = self._sx + (self._tx - self._sx) * blended
            self.y = self._sy + (self._ty - self._sy) * blended
            self._remember(self.x, self.y)
            self._trail_left = 0.45
        elif self._glide < 1.0:
            self._glide = 1.0
            self.x, self.y = self._tx, self._ty
            self._remember(self.x, self.y)
        elif self.trail and self._trail_left > 0.0:
            self._trail_left -= dt
            if self._trail_left <= 0.0:
                self.trail.clear()
        if self.effect:
            self.effect_age += dt
            limit = 1.4 if self.effect == "typing" else PULSE_SECONDS
            if self.effect_age >= limit and not self.holding:
                self.effect = ""
        if (
            self.visible
            and not self.holding
            and not self.suspended
            and not self.sticky
            and self.arrived
        ):
            self.idle += dt
        return self.frame()

    def frame(self) -> Frame:
        opacity = 1.0 if self.visible else 0.0
        if self.visible and not self.holding and not self.sticky and self.idle > IDLE_SECONDS:
            fade = (self.idle - IDLE_SECONDS) / FADE_SECONDS
            opacity = max(0.0, 1.0 - fade)
            if opacity <= 0.0:
                self.visible = False
                opacity = 0.0
        showing = self.visible and not self.suspended and opacity > 0.0
        effect_p = 0.0
        if self.effect in {"pulse", "double", "right"}:
            effect_p = min(1.0, self.effect_age / PULSE_SECONDS)
        elif self.effect == "typing":
            effect_p = self.effect_age
        return Frame(
            x=self.x,
            y=self.y,
            opacity=opacity if showing else 0.0,
            visible=showing,
            label=self.label if showing else "",
            bounds=self.bounds if showing else None,
            effect=self.effect if showing else "",
            effect_p=effect_p if showing else 0.0,
            phase=self.phase,
            holding=self.holding,
            arrived=self.arrived,
            trail=tuple(self.trail),
            scale=self.scale,
            show_label=self.show_label,
        )


class OverlayShow:
    """Apply protocol messages and emit ``arrived`` once the glide finishes."""

    def __init__(self, motion: CursorMotion | None = None) -> None:
        self.motion = motion if motion is not None else CursorMotion(reduce_motion=False)
        self.pending_seq: int | None = None
        self.quit = False

    def handle(self, message: dict) -> list[dict]:
        op = str(message.get("op") or "")
        seq = message.get("seq")
        if op == "configure":
            scale = message.get("scale") if "scale" in message else None
            labels = message.get("labels") if "labels" in message else None
            self.motion.set_style(scale, labels)
            return []
        if op == "propose":
            if message.get("sticky"):
                self.motion.sticky = True
            if "scale" in message or "labels" in message:
                self.motion.set_style(
                    message.get("scale") if "scale" in message else None,
                    message.get("labels") if "labels" in message else None,
                )
            origin = None
            if "ox" in message and "oy" in message:
                origin = (float(message["ox"]), float(message["oy"]))
            self.motion.propose(cue_from_message(message), origin)
            self.pending_seq = int(seq) if isinstance(seq, int) else None
            if self.motion.arrived and self.pending_seq is not None:
                token = self.pending_seq
                self.pending_seq = None
                return [{"event": "arrived", "seq": token}]
            return []
        if op == "act":
            self.motion.act(str(message.get("effect") or ""))
            return []
        if op == "release":
            self.motion.release()
            return []
        if op == "hide":
            self.motion.hide()
            self.pending_seq = None
            return []
        if op == "suspend":
            self.motion.suspend()
            if isinstance(seq, int):
                return [{"event": "suspended", "seq": seq}]
            return [{"event": "suspended"}]
        if op == "resume":
            self.motion.resume()
            return []
        if op == "quit":
            self.quit = True
            return [{"event": "quit"}]
        return []

    def tick(self, dt: float) -> tuple[Frame, list[dict]]:
        was = self.motion.arrived
        frame = self.motion.tick(dt)
        events: list[dict] = []
        if not was and self.motion.arrived and self.pending_seq is not None:
            events.append({"event": "arrived", "seq": self.pending_seq})
            self.pending_seq = None
        return frame, events
