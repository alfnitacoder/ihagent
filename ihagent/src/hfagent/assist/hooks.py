"""Glue between the agent loop and the assist features."""

from __future__ import annotations

from .cache import invalidate, lookup, record_tool, snapshot_block
from .errors import blocked_repeat, enrich_failure
from .security import findings_note
from .settings import security_check, style_audit
from .style import style_notes, with_file_header
from .summary import present

__all__ = [
    "blocked_repeat",
    "enrich_tool_error",
    "post_tool",
    "prepare_user_text",
    "present_to_user",
    "recall_cached",
    "snapshot_block",
    "with_file_header",
]


def prepare_user_text(user_text: str) -> tuple[str, str | None]:
    from ..memory import load_memory
    from .hosts import prepare_user_text as _prepare

    if not isinstance(user_text, str):
        return user_text, None
    return _prepare(user_text, load_memory())


def recall_cached(name: str, args: dict) -> str | None:
    if name not in {"list_dir", "read_file"} or not isinstance(args, dict):
        return None
    path = args.get("path") or "."
    if not isinstance(path, str):
        return None
    return lookup(name, path)


def enrich_tool_error(name: str, args: dict, result: str) -> str:
    return enrich_failure(name, args, result)


def present_to_user(agent, name: str, result: str) -> str:
    return present(agent, name, result)


def post_tool(
    name: str,
    args: dict,
    result: str,
    *,
    header_note: str = "",
    schedule=None,
) -> str:
    """Record successes and append install, style, and security notes."""
    if (result or "").lstrip().startswith("Error:"):
        return result
    record_tool(name, args, result)
    extras: list[str] = []
    if header_note:
        extras.append(header_note)
    if name in {"write_file", "edit_file"} and isinstance(args, dict):
        path = args.get("path")
        if isinstance(path, str) and path.strip():
            invalidate(path)
            content = _written_content(name, args, path)
            if style_audit():
                note = style_notes(path, content)
                if note:
                    extras.append(note)
            if security_check():
                note = findings_note(path, content)
                if note:
                    extras.append(note)
            if schedule is not None:
                install = schedule(path)
                if install:
                    extras.append(install)
    if not extras:
        return result
    return result.rstrip() + "\n" + "\n".join(extras)


def _written_content(name: str, args: dict, path: str) -> str:
    if name == "write_file" and isinstance(args.get("content"), str):
        return args["content"]
    from pathlib import Path

    try:
        return Path(path).expanduser().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
