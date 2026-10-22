"""What the model is told about mouse control. Read on every request.

The system prompt and the tool list are easy to freeze at session start.
These helpers read the live switch so a ``/mouse on`` in the same
conversation is visible on the next turn. Without that, the model keeps
asking for ``/mouse on`` and never moves the pointer, so the overlay
never appears.
"""

from __future__ import annotations

from .overlay_host import overlay_requested
from .session import get_session


def standing_instructions() -> str:
    """Paragraph appended to the system prompt. Reflects the current switch."""
    session = get_session()
    overlay = "on" if overlay_requested() else "off"
    if session.stopped:
        reason = session.stop_reason or "emergency stop"
        return (
            "Mouse control is STOPPED "
            f"({reason}). Do not call mouse tools. Ask the user to run "
            "/mouse on to resume."
        )
    if session.enabled():
        return (
            "Mouse control is ON. When the user asks to click, type, scroll, "
            "move, or look at another application, call the mouse_* tools now. "
            "Do not ask them to run /mouse on. "
            f"The ihmouse overlay is {overlay}."
        )
    return (
        "Mouse control is OFF. Ask the user to run /mouse on before any "
        "desktop action. Calling a mouse_* tool while it is off returns "
        "that same instruction. Do not treat the tools as enabled."
    )


def description_prefix() -> str:
    """First sentence of every mouse tool description sent to the model."""
    session = get_session()
    if session.enabled() and not session.stopped:
        return (
            "Mouse control is ON. Call this tool when the user asked. "
            "Do not tell the user to run /mouse on. "
        )
    if session.stopped:
        return (
            "Mouse control is STOPPED. Ask the user to run /mouse on before "
            "using the pointer. "
        )
    return (
        "Mouse control is OFF. Ask the user to run /mouse on. "
        "If you call this anyway, tell the user what the tool returns. "
    )


def toggle_note(arg: str) -> str | None:
    """A conversation note after ``/mouse on|off|stop|overlay``.

    The text starts with ``Mouse control is now`` so the transcript treats
    it as an internal nudge. Status, log, approval, label, and scale do not
    add a note.
    """
    parts = (arg or "").strip().split()
    if not parts:
        return None
    head = parts[0].lower()
    rest = " ".join(parts[1:]).strip().lower()
    if head == "on":
        return (
            "Mouse control is now ON. Use the mouse_* tools when the user asks "
            "to click, type, scroll, move, or look at another application. "
            "Do not ask them to run /mouse on. If they already asked for that, "
            "call the tools on your next turn. "
            + standing_instructions()
        )
    if head == "off":
        return (
            "Mouse control is now OFF. If the user asks for a desktop action, "
            "ask them to run /mouse on. Do not call mouse tools as if they were enabled."
        )
    if head == "stop":
        return (
            "Mouse control is now stopped (emergency stop). Do not call mouse "
            "tools until the user runs /mouse on."
        )
    if head == "overlay":
        if rest in {"", "status"}:
            return None
        if rest in {"on", "1", "true", "yes"}:
            return (
                "Mouse control is now showing the ihmouse overlay. "
                + standing_instructions()
            )
        if rest in {"off", "0", "false", "no"}:
            return (
                "Mouse control is now hiding the ihmouse overlay. The system "
                "pointer still moves when a mouse tool runs. "
                + standing_instructions()
            )
    return None
