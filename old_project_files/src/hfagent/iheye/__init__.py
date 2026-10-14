"""Floating iheye bubble for macOS.

While ihagent is running, a small always-on-top eye appears when the user
switches to another app and hides when they come back to the terminal that
is hosting ihagent. The bubble is a helper process; this module only supervises
it and never imports AppKit.
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
)
from .supervisor import supervisor as get_supervisor

__all__ = [
    "INSTALL_HINT",
    "STATES",
    "EyeStatus",
    "config_report_lines",
    "enabled_from_env",
    "handle_command",
    "publish",
    "start_if_enabled",
    "stop",
]

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
    """``/iheye``, ``/iheye on``, ``/iheye off``."""
    text = arg.strip().lower()
    if text in {"", "status"}:
        return status_text()
    if text == "on":
        os.environ["HFAGENT_IHEYE"] = "1"
        _persist("1")
        status = start_if_enabled()
        if status.running:
            return "iheye on"
        if status.reason == "not-macos":
            return "iheye only runs on macOS (saved HFAGENT_IHEYE=1)"
        return status.message or "iheye on"
    if text == "off":
        os.environ["HFAGENT_IHEYE"] = "0"
        _persist("0")
        stop()
        return "iheye off"
    return "usage: /iheye on|off"


def status_text() -> str:
    control = get_supervisor()
    if control.running:
        name = "terminal"
        if control.host is not None and control.host.app_name:
            name = control.host.app_name
        return f"iheye on · {name} · {control.state}"
    if not enabled_from_env():
        raw = os.environ.get("HFAGENT_IHEYE", "")
        if str(raw).strip() == "" and sys.platform != "darwin":
            return "iheye only runs on macOS"
        return "iheye off (HFAGENT_IHEYE=0). /iheye on to show it"
    if sys.platform != "darwin":
        return "iheye only runs on macOS"
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
    if sys.platform != "darwin":
        lines.append("iheye_platform=not-macos")
    elif pyobjc_available():
        lines.append("iheye_pyobjc=yes")
    else:
        lines.append(f"iheye_pyobjc=no  ({INSTALL_HINT})")
    return lines


def _persist(value: str) -> None:
    try:
        from ..config import save_user_setting

        save_user_setting("HFAGENT_IHEYE", value)
    except Exception:
        pass
