"""Per-project cache of recent listings, reads, and command results.

Stored under ``~/.hfagent/cache`` (or ``HFAGENT_CACHE_DIR``), keyed by the
project root. A read is reused only while the file's size and mtime match.
Writes drop the entries they touch.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from .settings import session_cache

_MANIFESTS = {
    "package.json", "pyproject.toml", "requirements.txt", "readme.md",
    "cargo.toml", "go.mod", "pipfile", "composer.json", "gemfile",
}
_MARKERS = (
    "package.json", "pyproject.toml", "requirements.txt",
    "go.mod", "Cargo.toml", ".git",
)
_SECRET_NAMES = {".env", ".env.local", "id_rsa", "credentials.json"}
MAX_ENTRIES = 24
MAX_TEXT = 8000


def _abs(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def project_root(start: Path | str | None = None) -> Path:
    """Nearest directory that looks like a project, else the start directory."""
    raw = Path.cwd() if start is None else Path(start)
    cur = _abs(raw)
    if cur.is_file() or (not cur.exists() and cur.suffix):
        cur = cur.parent
    for directory in [cur, *cur.parents]:
        try:
            if any((directory / name).exists() for name in _MARKERS):
                return directory
        except OSError:
            break
        if directory.parent == directory:
            break
    return cur


def normalize(path: str, root: Path | None = None) -> str:
    target = Path(path or ".").expanduser()
    if not target.is_absolute():
        base = root or Path.cwd()
        target = base / target
    return str(_abs(target))


def _cache_file(root: Path) -> Path:
    raw = os.environ.get("HFAGENT_CACHE_DIR", "").strip()
    base = Path(raw).expanduser() if raw else Path("~/.hfagent/cache").expanduser()
    digest = hashlib.sha256(str(_abs(root)).encode()).hexdigest()[:16]
    return base / f"{digest}.json"


def _empty(root: Path) -> dict:
    return {"root": str(_abs(root)), "entries": {}}


def load(root: Path) -> dict:
    path = _cache_file(root)
    if not path.is_file():
        return _empty(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty(root)
    if not isinstance(data, dict) or data.get("root") != str(_abs(root)):
        return _empty(root)
    data.setdefault("entries", {})
    return data


def save(root: Path, data: dict) -> None:
    path = _cache_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["root"] = str(_abs(root))
    data["updated"] = time.time()
    path.write_text(json.dumps(data), encoding="utf-8")


def _stat_sig(path: Path) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return {"mtime_ns": getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)), "size": st.st_size}


def _sensitive(path: Path) -> bool:
    name = path.name.lower()
    if name in _SECRET_NAMES or name.startswith(".env"):
        return True
    return name.endswith((".pem", ".key", ".p12"))


def remember(kind: str, path_or_cmd: str, text: str, *, root: Path | None = None) -> None:
    if not session_cache():
        return
    if kind in {"read_file", "list_dir"}:
        target = Path(normalize(path_or_cmd, root))
        if _sensitive(target):
            return
        base = project_root(target if target.exists() else target.parent)
        record_path = str(target)
        stat = _stat_sig(target)
    else:
        base = project_root(root)
        record_path = ""
        stat = None
    data = load(base)
    entries: dict = data.setdefault("entries", {})
    key = f"{kind}:{path_or_cmd}"
    entries[key] = {
        "kind": kind,
        "key": path_or_cmd,
        "path": record_path,
        "stat": stat,
        "text": (text or "")[:MAX_TEXT],
        "at": time.time(),
    }
    if len(entries) > MAX_ENTRIES:
        ordered = sorted(entries, key=lambda item: entries[item].get("at") or 0)
        for drop in ordered[: len(entries) - MAX_ENTRIES]:
            entries.pop(drop, None)
    if kind == "list_dir":
        data["layout"] = (text or "")[:2000]
        data["layout_path"] = record_path
        data["layout_stat"] = stat
    save(base, data)


def lookup(kind: str, path_or_cmd: str) -> str | None:
    """Return a fresh cached listing or file body. Commands are not replayed."""
    if not session_cache() or kind not in {"list_dir", "read_file"}:
        return None
    target = Path(normalize(path_or_cmd))
    if _sensitive(target):
        return None
    base = project_root(target if kind == "list_dir" or target.exists() else target.parent)
    data = load(base)
    record = data.get("entries", {}).get(f"{kind}:{path_or_cmd}")
    if not record:
        # also match a previous call that used a different spelling of the path
        for item in data.get("entries", {}).values():
            if item.get("kind") == kind and item.get("path") == str(target):
                record = item
                break
    if not record:
        return None
    current = _stat_sig(Path(record.get("path") or target))
    if current is None or current != record.get("stat"):
        return None
    text = record.get("text") or ""
    if not text or text.lstrip().startswith("Error:"):
        return None
    return (
        text
        + "\n(session cache: file unchanged — this note is not part of the file)"
    )


def invalidate(path: str) -> None:
    if not session_cache():
        return
    target = Path(path).expanduser()
    base = project_root(target if target.exists() else target.parent)
    data = load(base)
    entries: dict = data.get("entries", {})
    resolved = str(_abs(target)) if target.exists() else str(target)
    parent = str(_abs(target.parent))
    drop = []
    for key, record in entries.items():
        rec_path = record.get("path") or ""
        kind = record.get("kind")
        if kind in {"run_command", "ssh_run"}:
            drop.append(key)
            continue
        if rec_path in {resolved, parent} or key.endswith(path):
            drop.append(key)
    for key in drop:
        entries.pop(key, None)
    layout_path = data.get("layout_path") or ""
    if layout_path in {resolved, parent} or not entries:
        data.pop("layout", None)
        data.pop("layout_stat", None)
    if data.get("entries") or data.get("layout"):
        save(base, data)
    else:
        cache = _cache_file(base)
        if cache.is_file():
            try:
                cache.unlink()
            except OSError:
                pass


_SECRET_LINE = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|BEGIN .*PRIVATE KEY)\s*[:=]\s*\S{6,}"
    r"|sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}"
)


def _redact_excerpt(text: str) -> str:
    kept = []
    for line in (text or "").splitlines()[:12]:
        kept.append("[redacted]" if _SECRET_LINE.search(line) else line)
    return "\n".join(kept)


def _fresh(record: dict) -> bool:
    path = record.get("path")
    if not path:
        return True
    return _stat_sig(Path(path)) == record.get("stat")


def snapshot_block(start: Path | str | None = None) -> str:
    """Short project brief for the system prompt. Empty when the cache is cold."""
    if not session_cache():
        return ""
    root = project_root(start)
    warm(root)
    data = load(root)
    entries = data.get("entries") or {}
    if not entries and not data.get("layout"):
        return ""
    lines = [
        f"Project snapshot ({root}), from the session cache. "
        "Re-read a file before editing it if it may have changed."
    ]
    layout = data.get("layout") or ""
    layout_stat = data.get("layout_stat")
    layout_path = data.get("layout_path")
    if layout and layout_path and _stat_sig(Path(layout_path)) == layout_stat:
        lines.append("Layout:")
        lines.extend(layout.splitlines()[:25])
    shown = 0
    for record in entries.values():
        if record.get("kind") != "read_file" or not _fresh(record):
            continue
        name = Path(record.get("path") or "").name
        if name.lower() not in _MANIFESTS:
            continue
        excerpt = _redact_excerpt(record.get("text") or "")
        if not excerpt:
            continue
        lines.append(f"{name}:")
        lines.append(excerpt)
        shown += 1
        if shown >= 3:
            break
    commands = [
        record for record in entries.values()
        if record.get("kind") in {"run_command", "ssh_run"}
    ]
    if commands:
        latest = max(commands, key=lambda item: item.get("at") or 0)
        first = _redact_excerpt(latest.get("text") or "").splitlines()[:2]
        if first:
            lines.append("Last command:")
            lines.extend(first)
    text = "\n".join(lines).strip()
    if len(text) > 1800:
        return text[:1800] + "\n... [snapshot truncated]"
    return text


def warm(root: Path | None = None) -> None:
    """Fill root layout and key manifests once, without a tool call."""
    if not session_cache():
        return
    root = project_root(root)
    if not root.is_dir():
        return
    data = load(root)
    layout_path = data.get("layout_path")
    if not layout_path or _stat_sig(Path(layout_path)) != data.get("layout_stat"):
        try:
            entries = sorted(root.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        except OSError:
            entries = []
        lines = []
        for entry in entries[:80]:
            if entry.name.startswith(".git"):
                continue
            suffix = "/" if entry.is_dir() else ""
            kind = "d" if entry.is_dir() else "f"
            lines.append(f"{kind}          {entry.name}{suffix}")
        if lines:
            remember("list_dir", str(root), "\n".join(lines), root=root)
    for name in ("package.json", "pyproject.toml", "requirements.txt", "README.md"):
        path = root / name
        if not path.is_file() or path.stat().st_size > 20_000:
            continue
        if any(
            item.get("kind") == "read_file" and item.get("path") == str(_abs(path)) and _fresh(item)
            for item in load(root).get("entries", {}).values()
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        remember("read_file", str(path), text[:MAX_TEXT], root=root)


def describe(start: Path | str | None = None) -> str:
    root = project_root(start)
    data = load(root)
    entries = data.get("entries") or {}
    if not entries and not data.get("layout"):
        return f"(project cache is empty for {root})"
    kinds: dict[str, int] = {}
    for record in entries.values():
        kind = record.get("kind") or "?"
        kinds[kind] = kinds.get(kind, 0) + 1
    summary = ", ".join(f"{name}={count}" for name, count in sorted(kinds.items()))
    brief = snapshot_block(root)
    return f"Cache for {root} ({summary}).\n\n{brief}"


def clear(start: Path | str | None = None) -> str:
    root = project_root(start)
    path = _cache_file(root)
    if path.is_file():
        path.unlink()
        return f"cleared project cache for {root}"
    return f"project cache was already empty for {root}"


def record_tool(name: str, args: dict, result: str) -> None:
    if not session_cache() or not isinstance(args, dict):
        return
    if result.lstrip().startswith("Error:"):
        return
    if name in {"list_dir", "read_file"}:
        path = args.get("path") or "."
        if isinstance(path, str):
            remember(name, path, result)
    elif name == "run_command":
        command = args.get("command")
        if isinstance(command, str) and command.strip():
            remember("run_command", command.strip(), result)
    elif name == "ssh_run":
        host = args.get("host") or ""
        command = args.get("command") or ""
        if isinstance(command, str) and command.strip():
            remember("ssh_run", f"{host}: {command}".strip(), result)
