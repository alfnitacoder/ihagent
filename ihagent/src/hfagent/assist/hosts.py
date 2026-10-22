"""Suggest ssh_run when the user names a host stored in persistent memory."""

from __future__ import annotations

import re

from .settings import host_hints

_COMMON = {
    "http", "https", "localhost", "python", "node", "windows", "linux",
    "macos", "darwin", "ssh", "host", "server", "true", "false", "none",
    "null", "test", "tests", "admin", "root", "user", "home", "tmp",
}
_DIGITED = re.compile(r"\b([A-Za-z][A-Za-z0-9-]{1,32}\d[A-Za-z0-9-]*)\b")
_BACKTICK = re.compile(r"`([A-Za-z][A-Za-z0-9._-]{1,40})`")
_IS_HOST = re.compile(
    r"\b([A-Za-z][A-Za-z0-9._-]{2,40})\s+is\b[^\n]{0,120}\b"
    r"(?:host|server|box|kali|ssh|jump)\b",
    re.IGNORECASE,
)


def _hosty(token: str) -> bool:
    low = token.lower().strip(".")
    if low in _COMMON or len(low) < 3:
        return False
    if low.endswith((".py", ".js", ".md", ".txt", ".json", ".yml", ".yaml")):
        return False
    return True


def known_hosts(memory: str) -> list[str]:
    """Host-like tokens from ``memory.md``. Order is first-seen."""
    found: list[str] = []

    def add(token: str) -> None:
        cleaned = token.strip().strip("`").strip(".")
        if _hosty(cleaned) and cleaned not in found:
            found.append(cleaned)

    for match in _BACKTICK.finditer(memory or ""):
        add(match.group(1))
    for match in _DIGITED.finditer(memory or ""):
        add(match.group(1))
    for match in _IS_HOST.finditer(memory or ""):
        add(match.group(1))
    return found


def _extract_command(text: str) -> str | None:
    for pattern in (
        r"(?:run|exec|execute)\s*:\s*(.+)",
        r"`([^`]+)`",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        command = match.group(1).strip().strip(".")
        if command and not command.lower().startswith("ssh"):
            return command
    return None


def suggestion(user_text: str, memory: str) -> str | None:
    """A one-line ssh_run template when the request names a remembered host."""
    if not host_hints() or not user_text or not memory:
        return None
    mentioned = []
    for host in known_hosts(memory):
        if re.search(rf"(?<![\w.-]){re.escape(host)}(?![\w.-])", user_text):
            mentioned.append(host)
    if not mentioned:
        return None
    host = mentioned[0]
    command = _extract_command(user_text) or "<command>"
    safe = command.replace("\\", "\\\\").replace('"', '\\"')
    return (
        f"Host {host} is in persistent memory. Run this on that host with "
        f"ssh_run, not run_command. Template: "
        f"ssh_run(host={host!r}, command={command!r}). "
        f'JSON: {{"name": "ssh_run", "arguments": {{"host": "{host}", '
        f'"command": "{safe}"}}}}'
    )


def prepare_user_text(user_text: str, memory: str) -> tuple[str, str | None]:
    """Append the template to the user text so a weak model sees it."""
    hint = suggestion(user_text, memory)
    if not hint or not isinstance(user_text, str):
        return user_text, None
    return user_text.rstrip() + "\n\n" + hint, hint
