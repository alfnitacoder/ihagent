"""On/off switch, per-task grant, emergency stop, and the action log.

Nothing here reads the screen. The stop flag is set when a tool notices
the pointer in a corner, when the hotkey chord arrives, or when the user
runs ``/mouse stop``.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


class EmergencyStop(Exception):
    """Raised by the pointer when pyautogui's failsafe trips."""


class Session:
    def __init__(self) -> None:
        self.stopped = False
        self.stop_reason = ""
        self.task_granted = False
        self.lines: list[str] = []

    def enabled(self) -> bool:
        raw = os.environ.get("HFAGENT_MOUSE", "").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def approval_mode(self) -> str:
        raw = os.environ.get("HFAGENT_MOUSE_APPROVAL", "").strip().lower().replace("_", "-")
        if raw in {"per-task", "task", "once"}:
            return "per-task"
        return "per-action"

    def hotkey_spec(self) -> str:
        raw = os.environ.get("HFAGENT_MOUSE_STOP_HOTKEY", "").strip().lower()
        return raw or "ctrl+alt+esc"

    def begin_task(self) -> None:
        """A new user turn. Per-task approval has to be earned again."""
        self.task_granted = False

    def grant_task(self) -> None:
        if self.approval_mode() == "per-task":
            self.task_granted = True

    def needs_confirm(self, action: str) -> bool:
        """Whether this call should show the y/N prompt.

        Listing elements stays local and does not ask. Every other mouse
        tool asks, including while global approval is ``auto``, until a
        per-task grant is in effect. A stopped or disabled session does
        not ask: the tool returns the reason directly.
        """
        if action == "elements":
            return False
        if not self.enabled() or self.stopped:
            return False
        if self.approval_mode() == "per-task" and self.task_granted:
            return False
        return True

    def trip(self, reason: str) -> None:
        self.stopped = True
        self.stop_reason = reason.strip() or "emergency stop"

    def clear_stop(self) -> None:
        self.stopped = False
        self.stop_reason = ""

    def remember(self, line: str) -> None:
        self.lines.append(line)
        if len(self.lines) > 200:
            self.lines = self.lines[-200:]
        path = log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    def recent(self, limit: int = 30) -> list[str]:
        path = log_path()
        if path.is_file():
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
                if lines:
                    return lines[-limit:]
            except OSError:
                pass
        return self.lines[-limit:]


def log_path() -> Path:
    override = os.environ.get("HFAGENT_MOUSE_LOG", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hfagent" / "mouse.log"


def stamp(message: str, now: datetime | None = None) -> str:
    moment = now or datetime.now()
    return f"[mouse] {moment.strftime('%Y-%m-%d %H:%M:%S')} {message}"


_session = Session()


def get_session() -> Session:
    return _session


def reset_for_tests() -> None:
    """Drop stop, grant, and memory. Does not delete a log file."""
    global _session
    _session = Session()
