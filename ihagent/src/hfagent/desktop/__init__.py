"""Desktop mouse control. Off until ``/mouse on``.

Tools read the focused app's accessibility tree when the user asks, and
fall back to one screenshot plus a vision model when that tree is empty
or has no match. There is no background capture.
"""

from __future__ import annotations

from .command import USAGE, config_report_lines, handle_command, status_text
from .controller import DesktopController
from .session import get_session, reset_for_tests as _reset_session
from .vision import bind_chat

__all__ = [
    "USAGE",
    "begin_task",
    "bind_chat",
    "config_report_lines",
    "get_controller",
    "get_session",
    "handle_command",
    "reset_for_tests",
    "set_controller",
    "status_text",
]

_controller: DesktopController | None = None


def get_controller() -> DesktopController:
    global _controller
    if _controller is None:
        _controller = DesktopController(session=get_session())
    return _controller


def set_controller(controller: DesktopController | None) -> None:
    global _controller
    _controller = controller


def begin_task() -> None:
    """Call at the start of each user turn so per-task approval expires."""
    get_session().begin_task()


def reset_for_tests() -> None:
    from .hotkey import stop_hotkey
    from .overlay_host import reset_overlay

    stop_hotkey()
    reset_overlay()
    _reset_session()
    set_controller(None)
