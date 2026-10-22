"""Feature flags for the weaker-model assists. Defaults are on."""

from __future__ import annotations

import os

_ON = {"1", "true", "yes", "on"}
_OFF = {"0", "false", "no", "off"}


def env_flag(name: str, default: bool = True, environ: dict | None = None) -> bool:
    """Read a boolean setting.

    Unset or blank keeps ``default``. Unrecognised values also keep
    ``default`` so a typo does not silently disable a safety check.
    ``HFAGENT_ASSISTS=0`` forces every assist off.
    """
    env = os.environ if environ is None else environ
    if name != "HFAGENT_ASSISTS":
        master = env.get("HFAGENT_ASSISTS")
        if master is not None and str(master).strip().lower() in _OFF:
            return False
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    value = str(raw).strip().lower()
    if value in _ON:
        return True
    if value in _OFF:
        return False
    return default


def tool_guard(environ: dict | None = None) -> bool:
    return env_flag("HFAGENT_TOOL_GUARD", True, environ)


def summarize_output(environ: dict | None = None) -> bool:
    return env_flag("HFAGENT_SUMMARIZE_OUTPUT", True, environ)


def auto_install(environ: dict | None = None) -> bool:
    return env_flag("HFAGENT_AUTO_INSTALL", True, environ)


def session_cache(environ: dict | None = None) -> bool:
    return env_flag("HFAGENT_SESSION_CACHE", True, environ)


def style_audit(environ: dict | None = None) -> bool:
    return env_flag("HFAGENT_STYLE_AUDIT", True, environ)


def file_headers(environ: dict | None = None) -> bool:
    return env_flag("HFAGENT_FILE_HEADERS", True, environ)


def security_check(environ: dict | None = None) -> bool:
    return env_flag("HFAGENT_SECURITY_CHECK", True, environ)


def host_hints(environ: dict | None = None) -> bool:
    return env_flag("HFAGENT_HOST_HINTS", True, environ)


def summary_min_chars(environ: dict | None = None) -> int:
    env = os.environ if environ is None else environ
    raw = str(env.get("HFAGENT_SUMMARY_MIN_CHARS", "") or "").strip()
    try:
        value = int(raw or "1200")
    except ValueError:
        value = 1200
    return max(200, min(value, 100_000))


def report_lines(environ: dict | None = None) -> list[str]:
    """Lines for ``/config`` and ``--show-config``."""
    flags = (
        ("tool_guard", "HFAGENT_TOOL_GUARD", tool_guard(environ)),
        ("summarize_output", "HFAGENT_SUMMARIZE_OUTPUT", summarize_output(environ)),
        ("auto_install", "HFAGENT_AUTO_INSTALL", auto_install(environ)),
        ("session_cache", "HFAGENT_SESSION_CACHE", session_cache(environ)),
        ("style_audit", "HFAGENT_STYLE_AUDIT", style_audit(environ)),
        ("file_headers", "HFAGENT_FILE_HEADERS", file_headers(environ)),
        ("security_check", "HFAGENT_SECURITY_CHECK", security_check(environ)),
        ("host_hints", "HFAGENT_HOST_HINTS", host_hints(environ)),
    )
    lines = ["", "Assists (on unless set to 0/off/false/no; HFAGENT_ASSISTS=0 disables all):"]
    for label, var, enabled in flags:
        lines.append(f"{label}={'on' if enabled else 'off'}  [{var}]")
    lines.append(f"summary_min_chars={summary_min_chars(environ)}  [HFAGENT_SUMMARY_MIN_CHARS]")
    return lines
