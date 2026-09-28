"""Session persistence: auto-saved conversations under ~/.hfagent/sessions."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

SESSIONS_ROOT = Path("~/.hfagent/sessions").expanduser()


def _slug(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", cwd).strip("_") or "default"


def session_dir(cwd: str | None = None) -> Path:
    directory = SESSIONS_ROOT / _slug(cwd or os.getcwd())
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_session(
    messages: list[dict], model: str, cwd: str | None = None
) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = session_dir(cwd) / f"{stamp}.json"
    payload = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "messages": messages,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return path


def list_sessions(cwd: str | None = None, limit: int = 10) -> list[dict]:
    directory = session_dir(cwd)
    out: list[dict] = []
    for path in sorted(directory.glob("*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        first_user = next(
            (
                str(m.get("content", ""))
                for m in data.get("messages", [])
                if m.get("role") == "user"
            ),
            "",
        )
        out.append(
            {
                "name": path.stem,
                "path": str(path),
                "model": data.get("model", "?"),
                "preview": " ".join(first_user.split())[:60],
            }
        )
    return out


def load_session(name: str, cwd: str | None = None) -> list[dict] | None:
    """Load a session by stem name; 'last' picks the most recent one."""
    directory = session_dir(cwd)
    path = directory / f"{name}.json"
    if not path.exists() and name == "last":
        sessions = list_sessions(cwd, limit=1)
        if not sessions:
            return None
        path = Path(sessions[0]["path"])
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("messages")
