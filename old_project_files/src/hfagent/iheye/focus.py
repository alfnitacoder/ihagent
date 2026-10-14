"""Decide when the floating eye is on screen, and how a click focuses ihagent."""

from __future__ import annotations

from dataclasses import dataclass

from .host import HostInfo, focus_script, same_tty


@dataclass(frozen=True)
class FrontApp:
    pid: int
    bundle_id: str
    name: str


@dataclass(frozen=True)
class ActivationPlan:
    """What the helper does when the bubble is clicked."""

    applescript: str | None
    bundle_id: str
    pid: int | None
    app_name: str


def same_host(front: FrontApp, host: HostInfo) -> bool:
    """True when ``front`` is the app (or process) that is running ihagent."""
    if host.bundle_id and front.bundle_id and front.bundle_id == host.bundle_id:
        return True
    if host.pid and front.pid == host.pid:
        return True
    if front.pid and front.pid in host.ancestor_pids:
        return True
    front_name = (front.name or "").strip().lower()
    host_name = (host.app_name or "").strip().lower()
    if host_name and front_name and front_name == host_name:
        return True
    if host.kind == "cursor" and "cursor" in front_name:
        return True
    if host.kind == "vscode" and front_name in {"code", "visual studio code"}:
        return True
    if host.kind == "iterm2" and front_name in {"iterm", "iterm2"}:
        return True
    if host.kind == "terminal" and front_name == "terminal":
        return True
    return False


def eye_should_show(front: FrontApp | None, host: HostInfo, front_tty: str | None) -> bool:
    """Whether the bubble is visible.

    ``front_tty`` is the focused terminal's tty when we could read it.
    ``None`` means we did not check, so a focused host app hides the eye
    (app-level). A different tty on Terminal.app or iTerm2 keeps it visible.
    """
    if front is None or not same_host(front, host):
        return True
    if not host.window_level() or front_tty is None:
        return False
    if front_tty == "":
        return False
    return not same_tty(front_tty, host.tty)


def activation_plan(host: HostInfo) -> ActivationPlan:
    script = focus_script(host.kind, host.tty) if host.tty else None
    return ActivationPlan(
        applescript=script,
        bundle_id=host.bundle_id,
        pid=host.pid,
        app_name=host.app_name,
    )
