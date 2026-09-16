"""Filesystem tools: list, read, write, grep."""

from __future__ import annotations

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


class ListDir(Tool):
    name = "list_dir"
    description = "List a directory's entries (directories first)."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"}
        },
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
        "properties": {
            "path": {"type": "string"}
        },
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
    description = "Create or overwrite a file with content. Requires user approval."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string", "description": "Full file content."},
        },
        "required": ["path", "content"],
    }
    needs_approval = True

    def run(self, path: str, content: str) -> str:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {target}"


class Grep(Tool):
    name = "grep"
    description = "Search files with a regex; returns file:line: text matches."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
        },
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
