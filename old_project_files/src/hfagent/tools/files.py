"""Filesystem tools: list, read, write, edit, grep."""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from .base import Tool

SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".mypy_cache", ".pytest_cache", "dist", "build",
}
MAX_ENTRIES = 500
MAX_MATCHES = 80
MAX_FILE_CHARS = 40_000
MAX_DIFF_LINES = 60


def unified_diff(old: str, new: str, path: str) -> str:
    """Compact unified diff, truncated for display."""
    lines = list(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=2,
        )
    )
    text = "".join(lines)
    if len(lines) > MAX_DIFF_LINES:
        text = "".join(lines[:MAX_DIFF_LINES]) + f"... [+{len(lines) - MAX_DIFF_LINES} more lines]"
    return text if text else "(no changes)"


class ListDir(Tool):
    name = "list_dir"
    description = "List a directory's entries (directories first)."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }

    def run(self, path: str = ".") -> str:
        root = Path(path).expanduser()
        if not root.is_dir():
            return f"Error: not a directory: {root}"
        entries = sorted(root.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        lines: list[str] = []
        for entry in entries[:MAX_ENTRIES]:
            if entry.is_dir():
                lines.append(f"d          {entry.name}/")
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"f {size:>9} {entry.name}")
        if len(entries) > MAX_ENTRIES:
            lines.append(f"... ({len(entries) - MAX_ENTRIES} more entries)")
        return "\n".join(lines) if lines else "(empty directory)"


class ReadFile(Tool):
    name = "read_file"
    description = "Read a text file and return its content."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    def run(self, path: str) -> str:
        target = Path(path).expanduser()
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_FILE_CHARS:
            return text[:MAX_FILE_CHARS] + f"\n... [truncated, {len(text)} chars total]"
        return text


class WriteFile(Tool):
    name = "write_file"
    description = (
        "Create or overwrite a file with the full content. Prefer this when "
        "rewriting a whole component (React/JS/CSS) or adding several "
        "functions. Requires user approval."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string", "description": "Full file content."},
        },
        "required": ["path", "content"],
    }
    needs_approval = True

    def preview(self, path: str, content: str) -> str:
        target = Path(path).expanduser()
        if target.exists():
            try:
                old = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return f"(overwrite) {target}"
            return unified_diff(old, content, str(target))
        head = "\n".join(content.splitlines()[:30])
        more = "\n... [+more]" if len(content.splitlines()) > 30 else ""
        return f"(new file) {target}\n{head}{more}"

    def run(self, path: str, content: str) -> str:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {target}"


def _apply_edit(text: str, old_text: str, new_text: str) -> tuple[str | None, str]:
    """Replace old_text once. Exact match first, then stripped-line match.

    Returns (new_text_or_None, error). error is empty on success.
    """
    count = text.count(old_text)
    if count == 1:
        return text.replace(old_text, new_text, 1), ""
    if count > 1:
        return None, (
            f"Error: old_text appears {count} times; include more surrounding "
            "context to make it unique."
        )

    old_lines = [ln.strip() for ln in old_text.splitlines() if ln.strip()]
    if not old_lines:
        return None, (
            "Error: old_text not found. Read the file and copy the exact text."
        )
    file_lines = text.splitlines(keepends=True)
    stripped = [ln.strip() for ln in file_lines]
    hits = [
        i
        for i in range(len(stripped) - len(old_lines) + 1)
        if stripped[i : i + len(old_lines)] == old_lines
    ]
    if len(hits) > 1:
        return None, (
            f"Error: old_text appears {len(hits)} times; include more "
            "surrounding context to make it unique."
        )
    if len(hits) != 1:
        return None, (
            "Error: old_text not found. Read the file and copy the exact text."
        )
    start = hits[0]
    end = start + len(old_lines)
    prefix = "".join(file_lines[:start])
    suffix = "".join(file_lines[end:])
    block = new_text
    if block and suffix and not block.endswith("\n"):
        block += "\n"
    return prefix + block + suffix, ""


class EditFile(Tool):
    name = "edit_file"
    description = (
        "Replace a unique string in a file with new text. Prefer write_file "
        "when changing most of a file. old_text should match once (whitespace "
        "on a line can differ). Requires user approval."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string", "description": "Exact text to find."},
            "new_text": {"type": "string", "description": "Replacement text."},
        },
        "required": ["path", "old_text", "new_text"],
    }
    needs_approval = True

    def _load(self, path: str) -> tuple[Path, str] | tuple[None, str]:
        target = Path(path).expanduser()
        if not target.exists():
            return None, f"Error: file not found: {target}"
        try:
            return target, target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return None, f"Error: {type(exc).__name__}: {exc}"

    def preview(self, path: str, old_text: str, new_text: str) -> str:
        target, text = self._load(path)
        if target is None:
            return text
        updated, err = _apply_edit(text, old_text, new_text)
        if err:
            return f"(cannot preview: {err})"
        return unified_diff(text, updated or text, str(target))

    def run(self, path: str, old_text: str, new_text: str) -> str:
        target, text = self._load(path)
        if target is None:
            return text
        updated, err = _apply_edit(text, old_text, new_text)
        if err:
            return err
        target.write_text(updated or text, encoding="utf-8")
        return f"edited {target} ({len(old_text)} -> {len(new_text)} chars)"


class Grep(Tool):
    name = "grep"
    description = "Search files with a regex; returns file:line: text matches."
    parameters = {
        "type": "object",
        "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
        "required": ["pattern"],
    }

    def run(self, pattern: str, path: str = ".") -> str:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return f"Error: bad regex: {exc}"
        root = Path(path).expanduser()
        files = (
            [root]
            if root.is_file()
            else (p for p in root.rglob("*") if p.is_file() and not set(p.parts[:-1]) & SKIP_DIRS)
        )
        out: list[str] = []
        for file in files:
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    out.append(f"{file}:{lineno}: {line.strip()}")
                    if len(out) >= MAX_MATCHES:
                        return "\n".join(out) + "\n... [truncated]"
        return "\n".join(out) if out else "no matches"
