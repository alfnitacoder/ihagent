"""Assists for smaller models: safer tool use, summaries, and project checks.

Each feature is on by default. Set the matching ``HFAGENT_*`` variable to
``0``, ``off``, ``false``, or ``no`` to disable it. ``HFAGENT_ASSISTS=0``
turns the whole set off.
"""

from .settings import (
    auto_install,
    file_headers,
    host_hints,
    security_check,
    session_cache,
    style_audit,
    summarize_output,
    tool_guard,
)

__all__ = [
    "auto_install",
    "file_headers",
    "host_hints",
    "security_check",
    "session_cache",
    "style_audit",
    "summarize_output",
    "tool_guard",
]
