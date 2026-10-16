"""Run one mouse action with an injected tree, pointer, grabber, and vision client.

Real OS backends are loaded only when a dependency was not injected, and
only when a tool is called. There is no timer.
"""

from __future__ import annotations

import sys
from typing import Any, Callable

from .capture import CaptureError, fit_png
from .elements import TreeSnapshot, format_elements, key_is_navigation
from .geometry import CaptureRegion, Monitor, Rect, is_corner, on_screen, pixels_to_pointer
from .overlay_host import get_overlay, overlay_requested, peek_overlay
from .overlay_motion import Cue, effect_for, make_cue
from .permissions import screen_hint
from .pointer import MissingDesktopExtra, PyAutoPointer, normalize_keys
from .session import EmergencyStop, Session, get_session, stamp
from .targeting import Aim, NeedVision, coerce_element, has_target, resolve_aim
from .vision import (
    VisionSettings,
    build_image_messages,
    current_chat,
    describe_prompt,
    locate_prompt,
    openai_complete,
    parse_point,
    resolve_vision,
    vision_setup_message,
)

POINTER_ACTIONS = {"click", "double_click", "right_click", "move", "scroll", "type", "key"}
_CLICKS = {"click": (1, "left"), "double_click": (2, "left"), "right_click": (1, "right")}


def default_tree_reader() -> TreeSnapshot:
    if sys.platform == "darwin":
        from .ax_macos import read_tree
    elif sys.platform == "win32":
        from .uia_windows import read_tree
    else:
        from .atspi_linux import read_tree
    return read_tree()


def default_monitors() -> list[Monitor]:
    from .screens import read_monitors

    return read_monitors()


class DesktopController:
    def __init__(
        self,
        *,
        tree_reader: Callable[[], TreeSnapshot] | None = None,
        pointer: Any = None,
        grabber: Callable[[Rect], tuple[bytes, int, int]] | None = None,
        vision: Callable[[VisionSettings, list[dict[str, Any]]], str] | None = None,
        monitors: Callable[[], list[Monitor]] | None = None,
        session: Session | None = None,
        overlay: Any = None,
    ) -> None:
        self.tree_reader = tree_reader
        self.pointer = pointer
        self.grabber = grabber
        self.vision = vision
        self.monitors = monitors
        self.session = session if session is not None else get_session()
        self._overlay_client = overlay

    def needs_approval(self, action: str) -> bool:
        return self.session.needs_confirm(action)

    def stage(self, action: str, **kwargs: Any) -> None:
        """Show the robot on the target while the approval prompt is up.

        This does not move the system pointer and does not take a screenshot.
        A vision target is drawn later, after the user confirms.
        """
        try:
            self._stage(action, kwargs)
        except Exception:
            return

    def abandon_overlay(self) -> None:
        """The user declined. Hide the highlight."""
        self._hide_overlay()

    def _stage(self, action: str, kwargs: dict[str, Any]) -> None:
        if action not in POINTER_ACTIONS:
            return
        ui = self._ui()
        if ui is None:
            return
        aim = self._aim_quietly(action, kwargs)
        if aim is None:
            return
        ui.propose(self._cue(action, aim, kwargs), self._safe_position())

    def perform(self, action: str, **kwargs: Any) -> str:
        try:
            blocked = self._guard(action)
            if blocked:
                return self._fail(blocked)
            if action == "elements":
                return self._elements()
            if action == "describe":
                return self._describe(str(kwargs.get("question") or "Describe this screen."))
            if action in _CLICKS or action == "move":
                return self._point(action, kwargs)
            if action == "scroll":
                return self._scroll(kwargs)
            if action == "type":
                return self._type(kwargs)
            if action == "key":
                return self._key(kwargs)
        except EmergencyStop as exc:
            self._trip(str(exc) or "emergency stop")
            return self._fail(
                "Error: emergency stop. The pointer hit a screen corner. "
                "No further mouse actions will run. /mouse on to resume."
            )
        except MissingDesktopExtra:
            return self._fail(
                "Error: mouse control needs the optional desktop extra. "
                "Install it with: pip install 'ihagent[desktop]'"
            )
        except CaptureError as exc:
            return self._fail(str(exc))
        return self._fail(f"Error: unknown mouse action '{action}'.")

    def _guard(self, action: str) -> str | None:
        if not self.session.enabled():
            return (
                "Error: mouse control is off. It runs only after you ask and "
                "after /mouse on. Nothing is watching the screen."
            )
        if self.session.stopped:
            reason = self.session.stop_reason or "emergency stop"
            return (
                f"Error: emergency stop is on ({reason}). "
                "No further mouse actions will run. /mouse on to resume."
            )
        if action in POINTER_ACTIONS:
            pos = self._position()
            if pos is not None and is_corner(pos[0], pos[1], self._monitors()):
                self._trip("pointer in a screen corner")
                return (
                    "Error: emergency stop. The pointer is in a screen corner. "
                    "No further mouse actions will run. /mouse on to resume."
                )
        return None

    def _elements(self) -> str:
        snap = self._tree()
        body = format_elements(snap.elements, snap.app)
        if snap.error and not snap.elements:
            text = (
                f"{snap.error}\n{body}\n"
                "A click with target can fall back to a screenshot if a vision model is set."
            )
            return self._fail(text)
        self._log(f"listed {len(snap.elements)} elements in {snap.app or 'the focused app'}")
        if snap.error:
            return f"{body}\n{snap.error}"
        return body

    def _describe(self, question: str) -> str:
        snap = self._tree()
        window = snap.window
        if window is None:
            monitors = self._monitors()
            if not monitors:
                return self._fail("Error: no focused window or display to capture.")
            first = monitors[0]
            window = Rect(first.x, first.y, first.width, first.height)
        png, region = self._capture(window)
        settings = self._settings()
        if not settings.model:
            message = vision_setup_message(settings.chat_model)
            if snap.error:
                message = f"{snap.error}\n{message}"
            return self._fail(message)
        messages = build_image_messages(describe_prompt(question), png)
        try:
            text = (self._vision()(settings, messages) or "").strip()
        except Exception as exc:
            return self._fail(
                f"Error: vision model request failed ({type(exc).__name__}: {exc})."
            )
        if not text:
            return self._fail("Error: the vision model returned an empty description.")
        self.session.grant_task()
        app = snap.app or "focused window"
        line = stamp(f"described {app} via {settings.model}")
        self.session.remember(line)
        return f"{line}\nScreen ({app}): {text}"

    def _point(self, action: str, kwargs: dict[str, Any]) -> str:
        snap = self._tree()
        aimed = self._aim(snap, kwargs, required=True)
        if isinstance(aimed, str):
            return self._fail(aimed)
        blocked = self._destination_blocked(aimed)
        if blocked:
            return blocked
        self._announce(action, aimed, kwargs)
        self.session.grant_task()
        if action == "move":
            self._pointer().move(aimed.x, aimed.y)
            self._finish_overlay()
            return self._ok(
                f"moved to ({_fmt(aimed.x)}, {_fmt(aimed.y)}) via {aimed.source} ({aimed.label})"
            )
        clicks, button = _CLICKS[action]
        self._pointer().click(aimed.x, aimed.y, button, clicks)
        self._finish_overlay()
        verb = {"click": "clicked", "double_click": "double-clicked", "right_click": "right-clicked"}[
            action
        ]
        return self._ok(
            f"{verb} {aimed.label} at ({_fmt(aimed.x)}, {_fmt(aimed.y)}) via {aimed.source}"
        )

    def _scroll(self, kwargs: dict[str, Any]) -> str:
        amount = _amount(kwargs.get("amount", kwargs.get("clicks")))
        element = coerce_element(kwargs.get("element"))
        target = str(kwargs.get("target") or "")
        if has_target(element, target, kwargs.get("x"), kwargs.get("y")):
            snap = self._tree()
            aimed = self._aim(snap, kwargs, required=True)
            if isinstance(aimed, str):
                return self._fail(aimed)
            blocked = self._destination_blocked(aimed)
            if blocked:
                return blocked
            x, y = aimed.x, aimed.y
            where = aimed.label
            source = aimed.source
            shown = aimed
        else:
            pos = self._position() or (0.0, 0.0)
            x, y = pos
            where = "the pointer"
            source = "pointer"
            shown = Aim(x, y, source, where, None)
        self._announce("scroll", shown, kwargs)
        self.session.grant_task()
        self._pointer().scroll(x, y, amount)
        self._finish_overlay()
        return self._ok(
            f"scrolled {amount} at ({_fmt(x)}, {_fmt(y)}) via {source} ({where})"
        )

    def _type(self, kwargs: dict[str, Any]) -> str:
        text = str(kwargs.get("text") or "")
        if text == "":
            return self._fail("Error: mouse_type needs text.")
        if len(text) > 8000:
            return self._fail("Error: mouse_type refuses text longer than 8000 characters.")
        snap = self._tree()
        element = coerce_element(kwargs.get("element"))
        target = str(kwargs.get("target") or "")
        aimed: Aim | None = None
        if has_target(element, target, kwargs.get("x"), kwargs.get("y")):
            resolved = self._aim(snap, kwargs, required=True)
            if isinstance(resolved, str):
                return self._fail(resolved)
            if resolved.element is not None and resolved.element.password:
                return self._refuse_password(resolved.element)
            blocked = self._destination_blocked(resolved)
            if blocked:
                return blocked
            self._announce("type", resolved, kwargs)
            self._pointer().click(resolved.x, resolved.y, "left", 1)
            aimed = resolved
            # A named control we already checked is not a password field.
            # Re-read focus only for a coordinate or vision click, where the
            # field under the pointer is whatever ends up focused.
            if resolved.element is None:
                snap = self._tree()
                focused = snap.focused
                if focused is not None and focused.password:
                    return self._refuse_password(focused)
        else:
            focused = snap.focused
            if focused is not None and focused.password:
                return self._refuse_password(focused)
            if focused is not None:
                cx, cy = focused.center()
                aimed = Aim(cx, cy, "accessibility", focused.label(), focused)
            else:
                pos = self._safe_position() or (0.0, 0.0)
                aimed = Aim(pos[0], pos[1], "pointer", "the focused field", None)
            self._announce("type", aimed, kwargs)
        self.session.grant_task()
        self._pointer().type_text(text)
        self._finish_overlay()
        shown = text if len(text) <= 40 else text[:37] + "..."
        where = aimed.label if aimed is not None else "the focused field"
        return self._ok(f"typed {shown!r} into {where}")

    def _key(self, kwargs: dict[str, Any]) -> str:
        spec = str(kwargs.get("key") or kwargs.get("keys") or "").strip()
        if not spec:
            return self._fail("Error: mouse_key needs a key, for example enter or ctrl+s.")
        keys = normalize_keys(spec)
        if not keys:
            return self._fail("Error: mouse_key needs a key, for example enter or ctrl+s.")
        snap = self._tree()
        focused = snap.focused
        if focused is not None and focused.password and not key_is_navigation(spec):
            return self._refuse_password(focused)
        if focused is not None:
            cx, cy = focused.center()
            aimed = Aim(cx, cy, "accessibility", focused.label(), focused)
        else:
            pos = self._safe_position() or (0.0, 0.0)
            aimed = Aim(pos[0], pos[1], "pointer", spec, None)
        self._announce("key", aimed, kwargs)
        self.session.grant_task()
        self._pointer().press(keys)
        self._finish_overlay()
        return self._ok(f"pressed {spec}")

    def _ui(self):
        if not self.session.enabled() or not overlay_requested():
            return None
        if self._overlay_client is not None:
            return self._overlay_client
        return get_overlay()

    def _cue(self, action: str, aim: Aim, kwargs: dict[str, Any]) -> Cue:
        name = ""
        if aim.element is not None and aim.element.name.strip():
            name = aim.element.name.strip()
        elif aim.label:
            name = aim.label
        bounds = None
        if aim.element is not None and aim.element.width > 0 and aim.element.height > 0:
            bounds = Rect(aim.element.x, aim.element.y, aim.element.width, aim.element.height)
        return make_cue(
            action,
            aim.x,
            aim.y,
            name,
            bounds,
            text=str(kwargs.get("text") or ""),
            key=str(kwargs.get("key") or kwargs.get("keys") or ""),
        )

    def _announce(self, action: str, aim: Aim, kwargs: dict[str, Any]) -> None:
        ui = self._ui()
        if ui is None:
            return
        try:
            ui.propose(self._cue(action, aim, kwargs), self._safe_position())
            ui.wait_arrived(1.0)
            effect = effect_for(action)
            if effect:
                ui.act(effect)
                ui.wait_effect(0.16)
        except Exception:
            return

    def _finish_overlay(self) -> None:
        ui = self._ui()
        if ui is None:
            return
        try:
            ui.release()
        except Exception:
            return

    def _hide_overlay(self) -> None:
        client = self._overlay_client if self._overlay_client is not None else peek_overlay()
        if client is None:
            return
        try:
            client.hide()
        except Exception:
            return

    def _trip(self, reason: str) -> None:
        self.session.trip(reason)
        self._hide_overlay()

    def _safe_position(self) -> tuple[float, float] | None:
        try:
            return self._position()
        except Exception:
            return None

    def _onscreen(self, aim: Aim) -> Aim | None:
        monitors = self._monitors()
        if is_corner(aim.x, aim.y, monitors) or not on_screen(aim.x, aim.y, monitors):
            return None
        return aim

    def _aim_quietly(self, action: str, kwargs: dict[str, Any]) -> Aim | None:
        """Resolve a highlight without a screenshot or a pointer move."""
        snap = self._tree()
        element = coerce_element(kwargs.get("element"))
        target = str(kwargs.get("target") or "")
        pointed = has_target(element, target, kwargs.get("x"), kwargs.get("y"))
        if action == "key" or (action == "type" and not pointed) or (action == "scroll" and not pointed):
            focused = snap.focused
            if action == "type" and focused is not None and focused.password:
                return None
            spec = str(kwargs.get("key") or kwargs.get("keys") or "")
            if action == "key" and focused is not None and focused.password and not key_is_navigation(spec):
                return None
            if action != "scroll" and focused is not None and focused.width > 0:
                cx, cy = focused.center()
                return self._onscreen(Aim(cx, cy, "accessibility", focused.label(), focused))
            pos = self._safe_position() or (0.0, 0.0)
            label = "the pointer" if action == "scroll" else (spec or "the focused field")
            return self._onscreen(Aim(pos[0], pos[1], "pointer", label, None))
        resolved = resolve_aim(
            snap.elements,
            element=element,
            target=target.strip(),
            x=kwargs.get("x"),
            y=kwargs.get("y"),
        )
        if not isinstance(resolved, Aim):
            return None
        if action == "type" and resolved.element is not None and resolved.element.password:
            return None
        return self._onscreen(resolved)

    def _aim(self, snap: TreeSnapshot, kwargs: dict[str, Any], *, required: bool) -> Aim | str:
        element = coerce_element(kwargs.get("element"))
        target = str(kwargs.get("target") or "").strip()
        if not required and not has_target(element, target, kwargs.get("x"), kwargs.get("y")):
            return "Error: pass element, target, or x and y."
        resolved = resolve_aim(
            snap.elements,
            element=element,
            target=target,
            x=kwargs.get("x"),
            y=kwargs.get("y"),
        )
        if isinstance(resolved, NeedVision):
            return self._vision_aim(snap, resolved.description)
        if isinstance(resolved, str) and snap.error and "not in the current list" in resolved:
            return f"{resolved} {snap.error}"
        return resolved

    def _vision_aim(self, snap: TreeSnapshot, description: str) -> Aim | str:
        window = snap.window
        if window is None:
            message = "Error: no focused window to screenshot."
            if snap.error:
                message = f"{snap.error}\n{message}"
            return message
        try:
            png, region = self._capture(window)
        except CaptureError as exc:
            text = str(exc)
            if snap.error:
                text = f"{snap.error}\n{text}"
            return text
        settings = self._settings()
        if not settings.model:
            message = vision_setup_message(settings.chat_model)
            if snap.error:
                message = f"{snap.error}\n{message}"
            return message
        prompt = locate_prompt(description, region.pixel_width, region.pixel_height)
        messages = build_image_messages(prompt, png)
        try:
            raw = self._vision()(settings, messages)
        except Exception as exc:
            return f"Error: vision model request failed ({type(exc).__name__}: {exc})."
        parsed = parse_point(str(raw or ""), region.pixel_width, region.pixel_height)
        if isinstance(parsed, str):
            return parsed
        try:
            x, y = pixels_to_pointer(region, parsed[0], parsed[1])
        except ValueError as exc:
            return f"Error: {exc}"
        return Aim(x, y, "vision", description, None)

    def _capture(self, window: Rect) -> tuple[bytes, CaptureRegion]:
        ui = self._ui()
        if ui is not None:
            try:
                ui.suspend()
            except Exception:
                ui = None
        try:
            png, width, height = self._grabber()(window)
        finally:
            if ui is not None:
                try:
                    ui.resume()
                except Exception:
                    pass
        png, width, height = fit_png(png, width, height)
        if width <= 0 or height <= 0:
            raise CaptureError(screen_hint(sys.platform))
        return png, CaptureRegion.from_rect(window, width, height)

    def _destination_blocked(self, aimed: Aim) -> str | None:
        monitors = self._monitors()
        if is_corner(aimed.x, aimed.y, monitors):
            self._trip("move to a screen corner")
            return self._fail(
                "Error: emergency stop. Refusing to move the pointer into a screen corner. "
                "/mouse on to resume."
            )
        if not on_screen(aimed.x, aimed.y, monitors):
            return self._fail(
                f"Error: ({_fmt(aimed.x)}, {_fmt(aimed.y)}) is outside the displays."
            )
        return None

    def _refuse_password(self, element) -> str:
        self._hide_overlay()
        return self._fail(
            "Error: refusing to type into a password field "
            f"({element.label()}). Mouse control does not enter secrets."
        )

    def _tree(self) -> TreeSnapshot:
        reader = self.tree_reader or default_tree_reader
        try:
            snap = reader()
        except Exception as exc:
            return TreeSnapshot(error=f"Error: could not read the accessibility tree ({exc}).")
        return snap if isinstance(snap, TreeSnapshot) else TreeSnapshot(error="Error: accessibility tree was empty.")

    def _pointer(self):
        if self.pointer is None:
            self.pointer = PyAutoPointer()
        return self.pointer

    def _grabber(self) -> Callable[[Rect], tuple[bytes, int, int]]:
        if self.grabber is not None:
            return self.grabber
        from .capture import grab_rect

        return grab_rect

    def _vision(self):
        return self.vision or openai_complete

    def _monitors(self) -> list[Monitor]:
        reader = self.monitors or default_monitors
        try:
            found = list(reader() or [])
        except Exception:
            return []
        return found

    def _position(self) -> tuple[float, float] | None:
        try:
            return self._pointer().position()
        except MissingDesktopExtra:
            raise
        except Exception:
            return None

    def _settings(self) -> VisionSettings:
        base, model, key = current_chat()
        return resolve_vision(base, model, key)

    def _ok(self, message: str) -> str:
        line = stamp(message)
        self.session.remember(line)
        return line

    def _fail(self, message: str) -> str:
        text = message if message.startswith("Error:") else f"Error: {message}"
        detail = text.split("\n", 1)[0].removeprefix("Error: ").strip()
        line = stamp(detail)
        self.session.remember(line)
        return f"{text}\n{line}"

    def _log(self, message: str) -> None:
        self.session.remember(stamp(message))


def _amount(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 3


def _fmt(value: float) -> str:
    rounded = round(value)
    if abs(value - rounded) < 0.05:
        return str(int(rounded))
    return f"{value:.1f}"
