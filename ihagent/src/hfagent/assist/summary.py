"""Plain-language summaries of long shell and browser output."""

from __future__ import annotations

import re

from .settings import summarize_output, summary_min_chars

_INTERESTING = re.compile(
    r"(error|exception|traceback|fail|fatal|panic|assert|errno|"
    r"eaddrinuse|not found|warning|denied|refused)",
    re.IGNORECASE,
)
SUMMARY_TOOLS = {"run_command", "browser_read", "ssh_run"}


def is_long(text: str, *, min_chars: int | None = None) -> bool:
    limit = summary_min_chars() if min_chars is None else min_chars
    if len(text) >= limit:
        return True
    return text.count("\n") >= 40


def summarize(name: str, text: str) -> str:
    """Key lines, errors, and the exit code. The full text stays available."""
    lines = text.splitlines() or [text]
    exit_line = ""
    for line in lines[:6]:
        if line.lower().startswith("exit code:"):
            exit_line = line.strip()
            break
    interesting: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and _INTERESTING.search(stripped):
            interesting.append(stripped[:240])
        if len(interesting) >= 8:
            break
    nonempty = [line.strip() for line in lines if line.strip()]
    key: list[str] = []
    for line in nonempty[:3] + interesting + nonempty[-3:]:
        if line not in key:
            key.append(line)
    parts = [
        f"Summary of {name} ({len(lines)} lines, {len(text)} chars).",
    ]
    if exit_line:
        parts.append(exit_line)
    if interesting:
        parts.append("Errors and warnings:")
        parts.extend(f"  {line}" for line in interesting)
    parts.append("Key lines:")
    parts.extend(f"  {line}" for line in key[:12])
    if "BLOCKED:" in text:
        parts.append("BLOCKED: the identical call was not run again.")
    parts.append("Full output: /output")
    return "\n".join(parts)


def present(agent, name: str, result: str) -> str:
    """What the user sees. The model still receives ``result`` unchanged."""
    if name in SUMMARY_TOOLS:
        agent._last_full_output = result
    if not summarize_output() or name not in SUMMARY_TOOLS or not is_long(result):
        return result
    return summarize(name, result)


def last_full_output(agent) -> str:
    text = getattr(agent, "_last_full_output", "") or ""
    if not text:
        return "(no saved command or browser output yet)"
    return text
