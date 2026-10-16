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
    ) -> None:
        self.tree_reader = tree_reader
        self.pointer = pointer
        self.grabber = grabber
        self.vision = vision
        self.monitors = monitors
        self.session = session if session is not None else get_session()

    def needs_approval(self, action: str) -> bool:
        return self.session.needs_confirm(action)

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
            self.session.trip(str(exc) or "emergency stop")
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
                self.session.trip("pointer in a screen corner")
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
        self.session.grant_task()
        if action == "move":
            self._pointer().move(aimed.x, aimed.y)
            return self._ok(
                f"moved to ({_fmt(aimed.x)}, {_fmt(aimed.y)}) via {aimed.source} ({aimed.label})"
            )
        clicks, button = _CLICKS[action]
        self._pointer().click(aimed.x, aimed.y, button, clicks)
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
        else:
            pos = self._position() or (0.0, 0.0)
            x, y = pos
            where = "the pointer"
            source = "pointer"
        self.session.grant_task()
        self._pointer().scroll(x, y, amount)
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
        self.session.grant_task()
        self._pointer().type_text(text)
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
        self.session.grant_task()
        self._pointer().press(keys)
        return self._ok(f"pressed {spec}")

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
        png, width, height = self._grabber()(window)
        png, width, height = fit_png(png, width, height)
        if width <= 0 or height <= 0:
            raise CaptureError(screen_hint(sys.platform))
        return png, CaptureRegion.from_rect(window, width, height)

    def _destination_blocked(self, aimed: Aim) -> str | None:
        monitors = self._monitors()
        if is_corner(aimed.x, aimed.y, monitors):
            self.session.trip("move to a screen corner")
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
