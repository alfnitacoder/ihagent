"""Desktop pointer tools. They do nothing until the user runs ``/mouse on``.

Every action except ``mouse_elements`` asks before it runs, including when
global approval is ``auto``. Per-task approval (``/mouse approval per-task``)
asks once for the user turn.
"""

from __future__ import annotations

from typing import Any

from ..desktop import get_controller
from .base import Tool


_TARGET = {
    "element": {
        "type": "integer",
        "description": "Id from mouse_elements. The click hits that element's centre.",
    },
    "target": {
        "type": "string",
        "description": (
            "What to act on when you have no id. A unique accessibility name "
            "is clicked at its centre. If the tree is empty or has no match, "
            "one screenshot is sent to the vision model."
        ),
    },
    "x": {"type": "number", "description": "Pointer x, used only when element and target are omitted."},
    "y": {"type": "number", "description": "Pointer y, used only when element and target are omitted."},
}


def _schema(extra: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    properties: dict[str, Any] = dict(_TARGET)
    if extra:
        properties.update(extra)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _preview(action: str, kwargs: dict[str, Any]) -> str:
    bits = [f"mouse {action.replace('_', ' ')}"]
    if kwargs.get("element") not in (None, ""):
        bits.append(f"element {kwargs.get('element')}")
    if kwargs.get("target"):
        bits.append(f"target {kwargs.get('target')!r}")
    if kwargs.get("x") is not None and kwargs.get("y") is not None:
        bits.append(f"at ({kwargs.get('x')}, {kwargs.get('y')})")
    if kwargs.get("text"):
        shown = str(kwargs.get("text"))
        if len(shown) > 40:
            shown = shown[:37] + "..."
        bits.append(f"text {shown!r}")
    if kwargs.get("key") or kwargs.get("keys"):
        bits.append(str(kwargs.get("key") or kwargs.get("keys")))
    if kwargs.get("amount") is not None:
        bits.append(f"amount {kwargs.get('amount')}")
    if kwargs.get("question"):
        bits.append(repr(str(kwargs.get("question"))[:80]))
    return " ".join(bits)


class _MouseTool(Tool):
    action = ""
    force_approval = True

    def requires_approval(self, **kwargs: Any) -> bool:
        return get_controller().needs_approval(self.action)

    def preview(self, **kwargs: Any) -> str:
        try:
            get_controller().stage(self.action, **kwargs)
        except Exception:
            pass
        return _preview(self.action, kwargs)

    def abandon(self, **kwargs: Any) -> None:
        try:
            get_controller().abandon_overlay()
        except Exception:
            return None

    def run(self, **kwargs: Any) -> str:
        try:
            return get_controller().perform(self.action, **kwargs)
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"


class MouseElements(_MouseTool):
    name = "mouse_elements"
    action = "elements"
    force_approval = False
    description = (
        "List the focused app's accessibility elements as a numbered list "
        "of role, name, and bounds. Call this only when the user asked to "
        "look at or control another application. Does nothing while mouse "
        "control is off (/mouse on). Does not watch the screen in the background."
    )
    parameters = {"type": "object", "properties": {}}


class MouseDescribe(_MouseTool):
    name = "mouse_describe"
    action = "describe"
    description = (
        "Describe the focused window by sending one screenshot to the vision "
        "model. Call this only when the user asked what is on screen. "
        "Requires /mouse on. Does not run on a timer."
    )
    parameters = _schema(
        {"question": {"type": "string", "description": "What to ask about the screen."}},
        required=["question"],
    )


class MouseClick(_MouseTool):
    name = "mouse_click"
    action = "click"
    description = (
        "Click the focused app. Prefer element, an id from mouse_elements; "
        "the click hits the centre of that element. target is a description "
        "used when the accessibility tree is empty or has no match. "
        "Only when the user asked to click something. Requires /mouse on. "
        "Asks before it runs."
    )
    parameters = _schema()


class MouseDoubleClick(_MouseTool):
    name = "mouse_double_click"
    action = "double_click"
    description = (
        "Double-click. Same targeting as mouse_click (element id, then "
        "target description, then x and y). Only when the user asked. "
        "Requires /mouse on."
    )
    parameters = _schema()


class MouseRightClick(_MouseTool):
    name = "mouse_right_click"
    action = "right_click"
    description = (
        "Right-click. Same targeting as mouse_click. Only when the user "
        "asked. Requires /mouse on."
    )
    parameters = _schema()


class MouseMove(_MouseTool):
    name = "mouse_move"
    action = "move"
    description = (
        "Move the pointer. Same targeting as mouse_click. Moving into a "
        "screen corner is an emergency stop. Only when the user asked. "
        "Requires /mouse on."
    )
    parameters = _schema()


class MouseScroll(_MouseTool):
    name = "mouse_scroll"
    action = "scroll"
    description = (
        "Scroll at an element, a described target, or the current pointer. "
        "amount is positive for up and negative for down. Only when the "
        "user asked. Requires /mouse on."
    )
    parameters = _schema(
        {
            "amount": {
                "type": "integer",
                "description": "Wheel clicks. Positive scrolls up, negative scrolls down.",
            }
        }
    )


class MouseType(_MouseTool):
    name = "mouse_type"
    action = "type"
    description = (
        "Type text into the focused field, or into element / target first. "
        "Refuses password fields. Only when the user asked to type. "
        "Requires /mouse on."
    )
    parameters = _schema(
        {"text": {"type": "string", "description": "Text to type. Not accepted in a password field."}},
        required=["text"],
    )


class MouseKey(_MouseTool):
    name = "mouse_key"
    action = "key"
    description = (
        "Press a key or hotkey, for example enter or ctrl+s. Refuses to "
        "type into a password field (tab and escape are still allowed). "
        "Only when the user asked. Requires /mouse on."
    )
    parameters = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "A key or chord such as enter, tab, escape, ctrl+s, or command+c.",
            }
        },
        "required": ["key"],
    }
