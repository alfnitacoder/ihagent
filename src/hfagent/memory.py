"""Persistent long-term memory for the agent.

One global markdown file (~/.hfagent/memory/memory.md) holds durable facts
across sessions: hosts, preferences, project context. Loaded into the system
prompt at startup so every session starts already knowing your world.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

MEMORY_ROOT = Path("~/.hfagent/memory").expanduser()
MAX_MEMORY_CHARS = 6_000


def memory_path() -> Path:
    MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
    return MEMORY_ROOT / "memory.md"


def load_memory() -> str:
    path = memory_path()
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(text) > MAX_MEMORY_CHARS:
        # keep the most recent entries (appended at the end)
        return "(older entries trimmed)\n" + text[-MAX_MEMORY_CHARS:]
    return text


def save_fact(fact: str) -> str:
    """Append a timestamped fact; identical lines are not duplicated."""
    entry = " ".join(fact.split())
    if not entry:
        return "Error: empty fact"
    existing = load_memory()
    if entry in existing:
        return f"already remembered: {entry[:80]}"
    stamp = datetime.now().strftime("%Y-%m-%d")
    with memory_path().open("a", encoding="utf-8") as fh:
        fh.write(f"- [{stamp}] {entry}\n")
    return f"remembered: {entry[:80]}"


def clear_memory() -> str:
    path = memory_path()
    if path.exists():
        path.unlink()
        return "memory cleared"
    return "memory was already empty"
