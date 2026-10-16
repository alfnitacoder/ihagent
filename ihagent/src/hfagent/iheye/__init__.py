"""Floating iheye bubble.

While ihagent is running, a small always-on-top eye appears when the user
switches to another app and hides when they come back to the terminal that
is hosting ihagent. macOS paints with PyObjC. Windows and Linux paint with
PySide6. The bubble is a helper process; this module only supervises it and
does not import AppKit or Qt.
"""

from __future__ import annotations

import atexit
import os
import sys

from .appearance import STATES
from .host import HostInfo, detect_host
from .supervisor import (
    INSTALL_HINT,
    EyeStatus,
    EyeSupervisor,
    enabled_from_env,
    pyobjc_available,
    skin_name,
    look_enabled,
    summary_enabled,
    track_enabled,
)
from .supervisor import supervisor as get_supervisor

__all__ = [
    "INSTALL_HINT",
    "STATES",
    "EyeStatus",
    "bind_handlers",
    "clear_approval",
    "config_report_lines",
    "enabled_from_env",
    "handle_command",
    "publish",
    "publish_activity",
    "publish_approval",
    "publish_reply",
    "publish_summary",
    "start_if_enabled",
    "stop",
    "look_enabled",
    "skin_name",
    "summary_enabled",
    "track_enabled",
    "unbind_handlers",
]

USAGE = "usage: /iheye on|off | track on|off | summary on|off | look on|off"

_ATEXIT = False


def _ensure_atexit() -> None:
    global _ATEXIT
    if not _ATEXIT:
        atexit.register(stop)
        _ATEXIT = True


def publish(state: str) -> None:
    """Tell the overlay what ihagent is doing. Never raises."""
    if state not in STATES:
        return
    try:
        get_supervisor().set_state(state)
    except Exception:
        pass


def publish_activity(*, working: bool | None = None, failed: bool | None = None) -> None:
    """Bot-only flags. ``working`` is a running tool. ``failed`` turns the done pulse amber.

    Never raises. Unknown to the protocol state list, so grok, classic, and
    the brushed-metal eye keep the states they already had.
    """
    try:
        get_supervisor().note_activity(working=working, failed=failed)
    except Exception:
        pass


def publish_reply(text: str, final: bool = False) -> None:
    """Latest assistant text for the hover panel. Never raises."""
    try:
        get_supervisor().note_reply(text, final=final)
    except Exception:
        pass


def publish_summary(text: str) -> None:
    """First lines of a finished turn, for the drop-down card. Never raises."""
    try:
        get_supervisor().note_summary(text)
    except Exception:
        pass


def publish_approval(tool: str, preview: str) -> None:
    """Show the y/N card. Never raises."""
    try:
        get_supervisor().note_approval(tool, preview)
    except Exception:
        pass


def clear_approval() -> None:
    try:
        get_supervisor().note_approval("", "")
    except Exception:
        pass


def bind_handlers(on_chat, on_approval=None) -> None:
    """Route overlay chat and y/N into the running session. Never raises."""
    try:
        control = get_supervisor()
        control.on_chat = on_chat
        control.on_approval = on_approval
    except Exception:
        pass


def unbind_handlers() -> None:
    try:
        control = get_supervisor()
        control.on_chat = None
        control.on_approval = None
    except Exception:
        pass


def start_if_enabled(
    *,
    environ: dict[str, str] | None = None,
    platform_name: str | None = None,
    control: EyeSupervisor | None = None,
    host: HostInfo | None = None,
) -> EyeStatus:
    """Spawn the helper when this platform and ``HFAGENT_IHEYE`` allow it."""
    env = os.environ if environ is None else environ
    platform_name = sys.platform if platform_name is None else platform_name
    control = control or get_supervisor()
    control.platform_name = platform_name
    if not enabled_from_env(env, platform_name):
        status = EyeStatus(False, "disabled", "iheye off")
        control.status = status
        return status
    _ensure_atexit()
    if host is None:
        host = detect_host(environ=env)
    return control.start(host)


def stop() -> None:
    try:
        get_supervisor().stop()
    except Exception:
        pass


def handle_command(arg: str) -> str:
    """``/iheye``, ``on|off``, ``track``, ``summary``, and ``look``."""
    parts = arg.strip().lower().split()
    if not parts or parts[0] == "status":
        return status_text()
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    if head == "on":
        os.environ["HFAGENT_IHEYE"] = "1"
        _persist("HFAGENT_IHEYE", "1")
        _sync_flags_from_env()
        status = start_if_enabled()
        if status.running:
            return "iheye on"
        if status.reason == "unsupported":
            return "iheye runs on macOS, Windows, and Linux (saved HFAGENT_IHEYE=1)"
        return status.message or "iheye on"
    if head == "off":
        os.environ["HFAGENT_IHEYE"] = "0"
        _persist("HFAGENT_IHEYE", "0")
        stop()
        return "iheye off"
    if head == "track":
        return _toggle_flag("track", "HFAGENT_IHEYE_TRACK", rest)
    if head == "summary":
        return _toggle_flag("summary", "HFAGENT_IHEYE_SUMMARY", rest)
    if head == "look":
        return _toggle_flag("look", "HFAGENT_IHEYE_LOOK", rest)
    return USAGE


def _toggle_flag(label: str, env_name: str, rest: str) -> str:
    control = get_supervisor()
    if label == "track":
        current = control.track
    elif label == "summary":
        current = control.summary_enabled
    else:
        current = control.look_enabled
    if rest in {"", "status"}:
        state = "on" if current else "off"
        return f"iheye {label} {state}"
    if rest not in {"on", "off"}:
        return USAGE
    enabled = rest == "on"
    os.environ[env_name] = "1" if enabled else "0"
    _persist(env_name, "1" if enabled else "0")
    if label == "track":
        control.note_options(track=enabled)
    elif label == "summary":
        control.note_options(summary=enabled)
    else:
        control.note_options(look=enabled)
    return f"iheye {label} {rest}"


def _sync_flags_from_env() -> None:
    try:
        get_supervisor().note_options(
            track=track_enabled(),
            summary=summary_enabled(),
            look=look_enabled(),
        )
    except Exception:
        pass


def status_text() -> str:
    control = get_supervisor()
    if control.running:
        name = "terminal"
        if control.host is not None and control.host.app_name:
            name = control.host.app_name
        track = "track on" if control.track else "track off"
        summary = "summary on" if control.summary_enabled else "summary off"
        look = "look on" if control.look_enabled else "look off"
        return f"iheye on · {name} · {control.state} · {track} · {summary} · {look}"
    if not enabled_from_env():
        raw = os.environ.get("HFAGENT_IHEYE", "")
        if str(raw).strip() == "" and sys.platform != "darwin":
            return "iheye off. /iheye on to show it"
        return "iheye off (HFAGENT_IHEYE=0). /iheye on to show it"
    from .backend import backend_status

    _ready, reason, message = backend_status(sys.platform)
    if reason in {"missing-pyobjc", "missing-pyside", "unsupported"} and message:
        return message
    if control.status.reason == "missing-pyobjc" and control.status.message:
        return control.status.message
    if control.status.message and control.status.reason not in {"stopped", "disabled"}:
        return control.status.message
    return "iheye is not running. /iheye on to start it"


def config_report_lines() -> list[str]:
    from ..config import source_of

    raw = os.environ.get("HFAGENT_IHEYE")
    enabled = enabled_from_env()
    source = "default" if raw is None or str(raw).strip() == "" else source_of("HFAGENT_IHEYE")
    lines = [
        "",
        "iheye (floating eye when this terminal is in the background):",
        f"iheye={'on' if enabled else 'off'}  [{source}]",
    ]
    track_raw = os.environ.get("HFAGENT_IHEYE_TRACK")
    summary_raw = os.environ.get("HFAGENT_IHEYE_SUMMARY")
    track_source = "default" if track_raw is None or str(track_raw).strip() == "" else source_of(
        "HFAGENT_IHEYE_TRACK"
    )
    summary_source = (
        "default" if summary_raw is None or str(summary_raw).strip() == "" else source_of(
            "HFAGENT_IHEYE_SUMMARY"
        )
    )
    lines.append(f"iheye_track={'on' if track_enabled() else 'off'}  [{track_source}]")
    lines.append(f"iheye_summary={'on' if summary_enabled() else 'off'}  [{summary_source}]")
    skin_raw = os.environ.get("HFAGENT_IHEYE_SKIN")
    skin_source = (
        "default" if skin_raw is None or str(skin_raw).strip() == "" else source_of("HFAGENT_IHEYE_SKIN")
    )
    lines.append(f"iheye_skin={skin_name()}  [{skin_source}]")
    look_raw = os.environ.get("HFAGENT_IHEYE_LOOK")
    look_source = (
        "default" if look_raw is None or str(look_raw).strip() == "" else source_of("HFAGENT_IHEYE_LOOK")
    )
    lines.append(f"iheye_look={'on' if look_enabled() else 'off'}  [{look_source}]")
    from .backend import backend_name, qt_available

    kind = backend_name(sys.platform)
    if kind == "pyobjc":
        lines.append("iheye_backend=pyobjc")
        if pyobjc_available():
            lines.append("iheye_pyobjc=yes")
        else:
            lines.append(f"iheye_pyobjc=no  ({INSTALL_HINT})")
    elif kind == "qt":
        lines.append("iheye_backend=qt")
        if qt_available():
            lines.append("iheye_pyside=yes")
        else:
            from .backend import INSTALL_QT_HINT

            lines.append(f"iheye_pyside=no  ({INSTALL_QT_HINT})")
    else:
        lines.append("iheye_backend=unsupported")
    return lines


def _persist(name: str, value: str) -> None:
    try:
        from ..config import save_user_setting

        save_user_setting(name, value)
    except Exception:
        pass
