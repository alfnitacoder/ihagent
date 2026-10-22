"""Helpful context after a failed path tool, and a block on identical retries."""

from __future__ import annotations

import difflib
from pathlib import Path

from .settings import tool_guard

_PATH_TOOLS = {"read_file", "list_dir", "edit_file", "grep", "write_file"}
_SKIP_DESCEND = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".mypy_cache", ".pytest_cache",
}
_PATH_ERROR = (
    "not found",
    "no such file",
    "not a directory",
    "filenotfound",
    "errno 2",
    "cannot find",
    "is a directory",
)


def is_failure(result: str) -> bool:
    return (result or "").lstrip().startswith("Error:")


def arg_path(name: str, args: dict) -> str | None:
    if name not in _PATH_TOOLS or not isinstance(args, dict):
        return None
    raw = args.get("path")
    if isinstance(raw, str) and raw.strip():
        return raw
    return None


def _existing_dir(target: Path) -> Path | None:
    cursor = target if target.exists() else target.parent
    while not cursor.exists() and cursor != cursor.parent:
        cursor = cursor.parent
    if cursor.is_dir():
        return cursor
    return None


def path_context(path: str, *, limit: int = 40) -> str:
    """Directory listing of the nearest existing parent, plus close names."""
    target = Path(path).expanduser()
    folder = _existing_dir(target)
    if folder is None:
        return ""
    try:
        entries = sorted(folder.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
    except OSError:
        return ""
    names = [entry.name for entry in entries]
    lines = [f"Directory listing of {folder}:"]
    if not names:
        lines.append("  (empty)")
    for entry in entries[:limit]:
        suffix = "/" if entry.is_dir() else ""
        kind = "d" if entry.is_dir() else "f"
        lines.append(f"  {kind} {entry.name}{suffix}")
    if len(entries) > limit:
        lines.append(f"  ... ({len(entries) - limit} more)")

    wanted = target.name
    close = difflib.get_close_matches(wanted, names, n=5, cutoff=0.4)
    deeper: list[str] = []
    if len(close) < 3:
        for entry in entries:
            if not entry.is_dir() or entry.name in _SKIP_DESCEND:
                continue
            try:
                kids = [child.name for child in entry.iterdir()]
            except OSError:
                continue
            for hit in difflib.get_close_matches(wanted, kids, n=3, cutoff=0.55):
                deeper.append(str(entry / hit))
            if len(deeper) >= 5:
                break
    if close or deeper:
        lines.append("Closest paths:")
        for name in close:
            lines.append(f"  {folder / name}")
        for item in deeper:
            if item not in {str(folder / name) for name in close}:
                lines.append(f"  {item}")
    elif wanted:
        lines.append(f"No close name match for {wanted!r}.")
    text = "\n".join(lines)
    if len(text) > 2000:
        return text[:2000] + "\n... [listing truncated]"
    return text


def enrich_failure(name: str, args: dict, result: str) -> str:
    """Append a listing and close paths when a path-shaped call fails."""
    if not tool_guard() or not is_failure(result):
        return result
    if "Directory listing of " in result:
        return result
    path = arg_path(name, args)
    if not path:
        return result
    low = result.lower()
    if name == "edit_file" and "file not found" not in low and "not a directory" not in low:
        return result
    if name not in {"read_file", "list_dir", "edit_file"} and not any(
        token in low for token in _PATH_ERROR
    ):
        return result
    extra = path_context(path)
    if not extra:
        return result
    return result.rstrip() + "\n" + extra


def blocked_repeat(previous: dict, signature: tuple, hint: str) -> str | None:
    """Return a stand-in result when this exact call already failed.

    The tool is not run again. ``None`` means the call should proceed.
    """
    if not tool_guard():
        return None
    if previous.get("sig") != signature or not previous.get("failed"):
        return None
    if int(previous.get("n") or 0) < 1:
        return None
    body = str(previous.get("result") or "Error: the previous call failed").rstrip()
    note = (
        "BLOCKED: this identical call already failed, so it was not run again. "
        "Use the directory listing and closest paths, then change the path or the tool."
    )
    if "BLOCKED:" not in body:
        body = f"{body}\n{note}"
    hint_text = hint.strip()
    if hint_text and hint_text not in body:
        body = f"{body}\n{hint_text}"
    return body
