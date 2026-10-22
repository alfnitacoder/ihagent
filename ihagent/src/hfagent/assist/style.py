"""Style notes after UI edits, and a header on newly created source files."""

from __future__ import annotations

import re
from pathlib import Path

from .settings import file_headers, style_audit

_UI_EXT = {".html", ".htm", ".css", ".scss", ".sass", ".vue", ".jsx", ".tsx"}
_GENERIC_FONT = re.compile(
    r"font-family\s*:\s*(system-ui|arial|helvetica|sans-serif|inter|roboto)\b",
    re.IGNORECASE,
)


def looks_like_ui(path: str, content: str) -> bool:
    suffix = Path(path).suffix.lower()
    if suffix in {".html", ".htm", ".css", ".scss", ".sass", ".vue"}:
        return True
    if suffix in {".jsx", ".tsx"}:
        return True
    if suffix in {".js", ".ts"}:
        return bool(re.search(
            r"<[A-Za-z]|className|document\.|querySelector|@media|font-family|background",
            content,
        ))
    return False


def style_notes(path: str, content: str) -> str:
    """Concise suggestions. Empty when the file is fine or not a UI file."""
    if not style_audit() or not looks_like_ui(path, content):
        return ""
    low = content.lower()
    notes: list[str] = []
    imported = any(token in low for token in (
        "fonts.googleapis", "fonts.gstatic", "@font-face", "@import",
    ))
    has_family = "font-family" in low
    if not imported and not has_family:
        notes.append("no font import — add a Google Fonts link or @import, then set font-family")
    elif _GENERIC_FONT.search(content) and not imported:
        notes.append("font stack looks generic — import one expressive face (not Inter, Roboto, or Arial alone)")
    has_background = "background" in low
    has_gradient = "gradient" in low
    if not has_background:
        notes.append("no background — add a gradient, pattern, or layered background")
    elif not has_gradient and "url(" not in low:
        notes.append("background is a flat fill — a gradient or image reads as more finished")
    if not any(token in low for token in ("transition", "animation", "@keyframes")):
        notes.append("no transitions — add a short transition on hover for links or buttons")
    if not notes:
        return ""
    name = Path(path).name
    body = "\n".join(f"- {note}" for note in notes)
    return (
        f"style audit ({name}):\n{body}\n"
        "(suggestions only; opt out with HFAGENT_STYLE_AUDIT=0)"
    )


def header_for(path: str) -> str | None:
    """A one-line comment plus a tiny docs snippet, or None for data files."""
    target = Path(path)
    name = target.name
    suffix = target.suffix.lower()
    if suffix == ".py":
        return (
            f"# {name} — created by IHAgent.\n"
            f'"""{name}.\n\n'
            "Replace this with what the module does.\n"
            '"""\n\n'
        )
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".java", ".go", ".rs"}:
        return f"/* {name} — created by IHAgent.\n   Docs: say what this file is for.\n*/\n\n"
    if suffix in {".c", ".h", ".cpp"}:
        return f"/* {name} — created by IHAgent. */\n\n"
    if suffix in {".html", ".htm", ".vue"}:
        return f"<!-- {name} — created by IHAgent.\n     Docs: say what this page is for. -->\n"
    if suffix == ".sh":
        return f"#!/usr/bin/env bash\n# {name} — created by IHAgent.\n"
    if suffix == ".rb":
        return f"# {name} — created by IHAgent.\n"
    return None


def apply_header(path: str, content: str, *, existed: bool) -> tuple[str, str]:
    """Return ``(content, note)``. Existing files and opted-out sessions are unchanged."""
    if existed or not file_headers():
        return content, ""
    header = header_for(path)
    if not header:
        return content, ""
    stripped = content.lstrip()
    if stripped.startswith(("#!", "#", "//", "/*", "<!--", '"""', "'''", "<!")):
        return content, ""
    note = (
        f"added a header comment and a short docs snippet to new file "
        f"{Path(path).name} (HFAGENT_FILE_HEADERS=0 to skip)"
    )
    return header + content, note


def with_file_header(name: str, args: dict) -> tuple[dict, str]:
    """Rewrite ``write_file`` content when the path does not exist yet."""
    if name != "write_file" or not isinstance(args, dict):
        return args, ""
    path = args.get("path")
    content = args.get("content")
    if not isinstance(path, str) or not isinstance(content, str):
        return args, ""
    target = Path(path).expanduser()
    if target.exists():
        return args, ""
    updated, note = apply_header(path, content, existed=False)
    if updated == content:
        return args, ""
    copied = dict(args)
    copied["content"] = updated
    return copied, note
