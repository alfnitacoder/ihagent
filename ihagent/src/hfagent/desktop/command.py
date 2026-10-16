"""``/mouse`` — turn desktop control on, set approval, show the log, or stop."""

from __future__ import annotations

import os
import sys

from ..config import mask_secret, source_of
from .hotkey import start_hotkey, stop_hotkey
from .overlay_host import hide_overlay, overlay_problem, overlay_requested, rearm_overlay
from .session import get_session
from .vision import current_chat, resolve_vision


USAGE = "usage: /mouse on|off | overlay on|off | approval per-action|per-task | log | stop"


def handle_command(arg: str) -> str:
    parts = arg.strip().split()
    if not parts or parts[0] == "status":
        return status_text()
    head = parts[0].lower()
    rest = " ".join(parts[1:]).strip().lower()
    if head == "on":
        return _turn_on()
    if head == "off":
        return _turn_off()
    if head == "stop":
        get_session().trip("user /mouse stop")
        hide_overlay()
        return "mouse emergency stop. No further actions until /mouse on."
    if head == "overlay":
        return _overlay(rest)
    if head == "log":
        return _log_text()
    if head == "approval":
        return _approval(rest)
    return USAGE


def status_text() -> str:
    session = get_session()
    if not session.enabled():
        return (
            "mouse off. /mouse on to allow desktop actions when you ask. "
            "Nothing is watching the screen."
        )
    settings = resolve_vision(*current_chat())
    vision = settings.model or "(unset — text model cannot look)"
    host = settings.base_url or "(current host)"
    approval = session.approval_mode()
    overlay = "on" if overlay_requested() else "off"
    line = (
        f"mouse on · overlay {overlay} · approval {approval} · stop {session.hotkey_spec()} "
        f"or a screen corner · vision {vision} @ {host}"
    )
    problem = overlay_problem()
    if problem and overlay_requested():
        line = f"{line} · {problem}"
    if session.stopped:
        reason = session.stop_reason or "emergency stop"
        return f"{line} · EMERGENCY STOP ({reason}). /mouse on to resume."
    return line


def config_report_lines() -> list[str]:
    session = get_session()
    settings = resolve_vision(*current_chat())

    def src(name: str, fallback: str) -> str:
        if os.environ.get(name):
            return source_of(name)
        return fallback

    if os.environ.get("HFAGENT_VISION_API_KEY"):
        key_shown = mask_secret(os.environ.get("HFAGENT_VISION_API_KEY", ""))
    else:
        key_shown = "(current key)"
    return [
        "",
        "mouse (desktop pointer; off until /mouse on, never watches in the background):",
        f"mouse={'on' if session.enabled() else 'off'}  [{src('HFAGENT_MOUSE', 'default')}]",
        f"mouse_approval={session.approval_mode()}  [{src('HFAGENT_MOUSE_APPROVAL', 'default')}]",
        f"mouse_stop_hotkey={session.hotkey_spec()}  [{src('HFAGENT_MOUSE_STOP_HOTKEY', 'default')}]",
        f"mouse_overlay={'on' if overlay_requested() else 'off'}  [{src('HFAGENT_MOUSE_OVERLAY', 'default')}]",
        f"vision_model={settings.model or '(unset)'}  [{settings.model_source}]",
        f"vision_base_url={settings.base_url or '(current host)'}  [{settings.url_source}]",
        f"vision_api_key={key_shown}  [{settings.key_source}]",
        f"mouse_platform={_platform_name()}",
    ]


def _turn_on() -> str:
    os.environ["HFAGENT_MOUSE"] = "1"
    _persist("HFAGENT_MOUSE", "1")
    session = get_session()
    session.clear_stop()
    session.begin_task()
    rearm_overlay()
    note = start_hotkey(session.hotkey_spec())
    if note:
        return f"mouse on. {note}"
    return (
        f"mouse on. Stop with {session.hotkey_spec()} or by moving the pointer "
        "to a screen corner. Actions still ask before they run."
    )


def _turn_off() -> str:
    os.environ["HFAGENT_MOUSE"] = "0"
    _persist("HFAGENT_MOUSE", "0")
    stop_hotkey()
    hide_overlay()
    get_session().clear_stop()
    return "mouse off. Nothing is watching the screen."


def _overlay(rest: str) -> str:
    if rest in {"", "status"}:
        return f"mouse overlay {'on' if overlay_requested() else 'off'}"
    if rest in {"on", "1", "true", "yes"}:
        os.environ["HFAGENT_MOUSE_OVERLAY"] = "1"
        _persist("HFAGENT_MOUSE_OVERLAY", "1")
        return "mouse overlay on. The robot shows while mouse control is on."
    if rest in {"off", "0", "false", "no"}:
        os.environ["HFAGENT_MOUSE_OVERLAY"] = "0"
        _persist("HFAGENT_MOUSE_OVERLAY", "0")
        hide_overlay()
        return "mouse overlay off. The system pointer still moves when you confirm an action."
    return USAGE


def _approval(rest: str) -> str:
    session = get_session()
    if rest in {"", "status"}:
        return f"mouse approval {session.approval_mode()}"
    if rest in {"per-action", "action", "each"}:
        os.environ["HFAGENT_MOUSE_APPROVAL"] = "per-action"
        _persist("HFAGENT_MOUSE_APPROVAL", "per-action")
        session.begin_task()
        return "mouse approval per-action"
    if rest in {"per-task", "task", "once"}:
        os.environ["HFAGENT_MOUSE_APPROVAL"] = "per-task"
        _persist("HFAGENT_MOUSE_APPROVAL", "per-task")
        session.begin_task()
        return "mouse approval per-task"
    return USAGE


def _log_text() -> str:
    lines = get_session().recent(30)
    if not lines:
        return "mouse log is empty"
    return "\n".join(lines)


def _persist(name: str, value: str) -> None:
    try:
        from ..config import save_user_setting

        save_user_setting(name, value)
    except Exception:
        pass


def _platform_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    return "linux"
