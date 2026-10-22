"""Session persistence: auto-saved conversations under ~/.hfagent/sessions.

Files stay ``YYYYMMDD-HHMMSS.json``. Extra keys (``title``, ``title_custom``,
``pinned``, ``archived``) are optional, so sessions written by older versions
still load. A conversation keeps updating one file once its name is known;
``/clear`` starts a new file.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

SESSIONS_ROOT = Path("~/.hfagent/sessions").expanduser()
TITLE_LIMIT = 40
PREVIEW_LIMIT = 60
SESSION_PAGE = 40
_STAMP = re.compile(r"^(\d{8})-(\d{6})")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
# Mirrors the TUI nudge prefixes so a saved correction is not used as a title.
_NUDGE_SNIPPETS = (
    "Do not propose commands",
    "Never write tool_response",
    "You invented a result",
    "You announced:",
    "Continue the task with your tools",
    "Your previous response was empty",
    "do it yourself now with a tool call",
    "Mouse control is now",
)

_CACHE: dict[str, tuple[int, int, dict]] = {}
_LOCK = threading.Lock()


def session_clock() -> datetime:
    """Clock used for 'Today' / 'Yesterday'. Tests replace this."""
    return datetime.now()


def _slug(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", cwd).strip("_") or "default"


def session_dir(cwd: str | None = None) -> Path:
    directory = SESSIONS_ROOT / _slug(cwd or os.getcwd())
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _safe_name(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and not name.startswith(".")


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") in (None, "text"):
                    parts.append(str(part.get("text") or ""))
        return " ".join(p for p in parts if p)
    return ""


def _is_nudge(text: str) -> bool:
    return any(snippet in text for snippet in _NUDGE_SNIPPETS)


def trim_title(text: str, limit: int = TITLE_LIMIT) -> str:
    """Collapse whitespace and keep titles at about ``limit`` characters."""
    collapsed = " ".join(str(text).split())
    if not collapsed:
        return "untitled"
    if len(collapsed) <= limit:
        return collapsed
    cut = max(1, limit - 1)
    return collapsed[:cut].rstrip() + "…"


def derive_title(messages: list | None, limit: int = TITLE_LIMIT) -> str:
    """Title a session from its first real user message."""
    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = _message_text(message.get("content"))
        if not text.strip() or _is_nudge(text):
            continue
        return trim_title(text, limit)
    return "untitled"


def message_count(messages: list | None) -> int:
    """Count stored messages other than the system prompt."""
    return sum(
        1
        for message in messages or []
        if isinstance(message, dict) and message.get("role") != "system"
    )


def short_model(model: object, limit: int = 14) -> str:
    name = str(model or "?").split("/")[-1].strip() or "?"
    if len(name) <= limit:
        return name
    return name[: max(1, limit - 1)].rstrip() + "…"


def _naive(when: datetime) -> datetime:
    if when.tzinfo is None:
        return when
    return when.astimezone().replace(tzinfo=None)


def parse_stamp(name: str) -> datetime | None:
    match = _STAMP.match(name or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def parse_saved_at(value: object, name: str = "") -> datetime | None:
    if isinstance(value, str) and value.strip():
        try:
            return _naive(datetime.fromisoformat(value.strip()))
        except ValueError:
            pass
    return parse_stamp(name)


def day_heading(when: datetime, now: datetime) -> str:
    """'Today', 'Yesterday', or 'Oct 12' (with the year when it differs)."""
    when = _naive(when)
    now = _naive(now)
    if when.date() == now.date():
        return "Today"
    if when.date() == now.date() - timedelta(days=1):
        return "Yesterday"
    month = _MONTHS[when.month - 1]
    if when.year == now.year:
        return f"{month} {when.day}"
    return f"{month} {when.day}, {when.year}"


def clock_label(when: datetime) -> str:
    when = _naive(when)
    return f"{when.hour:02d}:{when.minute:02d}"


def exact_label(when: datetime) -> str:
    when = _naive(when)
    return when.strftime("%Y-%m-%d %H:%M:%S")


def _first_user_preview(messages: list) -> str:
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = " ".join(_message_text(message.get("content")).split())
        if text:
            return text[:PREVIEW_LIMIT]
    return ""


def _invalidate(path: Path) -> None:
    with _LOCK:
        _CACHE.pop(str(path), None)


def _read_payload(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_payload(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    _invalidate(path)


def _record_from(path: Path, data: dict) -> dict:
    messages = data.get("messages")
    if not isinstance(messages, list):
        messages = []
    custom = bool(data.get("title_custom"))
    stored = data.get("title")
    if custom and isinstance(stored, str) and stored.strip():
        title = trim_title(stored)
    else:
        title = derive_title(messages)
    when = parse_saved_at(data.get("saved_at"), path.stem)
    return {
        "name": path.stem,
        "path": str(path),
        "model": data.get("model") or "?",
        "preview": _first_user_preview(messages),
        "title": title,
        "title_custom": custom,
        "saved_at": data.get("saved_at") or "",
        "when": when,
        "message_count": message_count(messages),
        "pinned": bool(data.get("pinned")),
        "archived": bool(data.get("archived")),
    }


def _load_record(path: Path) -> dict | None:
    try:
        stat = path.stat()
    except OSError:
        _invalidate(path)
        return None
    key = str(path)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == stat.st_mtime_ns and hit[1] == stat.st_size:
            return dict(hit[2])
    data = _read_payload(path)
    if data is None:
        return None
    record = _record_from(path, data)
    with _LOCK:
        try:
            stat = path.stat()
        except OSError:
            return dict(record)
        _CACHE[key] = (stat.st_mtime_ns, stat.st_size, record)
    return dict(record)


def _fresh_path(directory: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"{stamp}.json"
    suffix = 2
    while path.exists():
        path = directory / f"{stamp}-{suffix}.json"
        suffix += 1
    return path


def _existing_path(name: str, cwd: str | None) -> Path | None:
    if name == "last":
        found = list_sessions(cwd, limit=1, include_archived=True)
        if not found:
            return None
        return Path(found[0]["path"])
    if not _safe_name(name):
        return None
    path = session_dir(cwd) / f"{name}.json"
    if path.is_file():
        return path
    return None


def save_session(
    messages: list[dict],
    model: str,
    cwd: str | None = None,
    *,
    name: str | None = None,
    title: str | None = None,
) -> Path:
    """Write a session. Pass ``name`` to update that file instead of creating one.

    ``title`` marks the name as user-chosen. An update keeps a custom title,
    pin, and archive flag already stored in the file.
    """
    directory = session_dir(cwd)
    existing: dict | None = None
    if name == "last":
        path = _existing_path("last", cwd)
        if path is None:
            path = _fresh_path(directory)
        else:
            existing = _read_payload(path) or {}
    elif name and _safe_name(name):
        path = directory / f"{name}.json"
        if path.is_file():
            existing = _read_payload(path) or {}
    else:
        path = _fresh_path(directory)

    payload = dict(existing or {})
    payload["saved_at"] = datetime.now().isoformat(timespec="seconds")
    payload["model"] = model
    payload["messages"] = messages
    if title is not None and str(title).strip():
        payload["title"] = trim_title(str(title))
        payload["title_custom"] = True
    elif payload.get("title_custom") and str(payload.get("title") or "").strip():
        payload["title"] = trim_title(str(payload.get("title")))
        payload["title_custom"] = True
    else:
        payload["title"] = derive_title(messages)
        payload["title_custom"] = False
    payload["pinned"] = bool(payload.get("pinned"))
    payload["archived"] = bool(payload.get("archived"))
    _write_payload(path, payload)
    return path


def list_sessions(
    cwd: str | None = None,
    limit: int | None = None,
    *,
    include_archived: bool = False,
) -> list[dict]:
    """Newest activity first. Archived sessions are omitted unless asked for.

    ``limit`` caps how many records are returned. ``None`` returns every match.
    Metadata is cached by path, mtime, and size so a large folder stays cheap
    to repaint.
    """
    directory = session_dir(cwd)
    records: list[dict] = []
    for path in directory.glob("*.json"):
        record = _load_record(path)
        if record is None:
            continue
        if record["archived"] and not include_archived:
            continue
        records.append(record)
    records.sort(
        key=lambda record: (
            record["when"] is not None,
            record["when"] or datetime.min,
            record["name"],
        ),
        reverse=True,
    )
    if limit is not None:
        records = records[: max(0, limit)]
    return records


def delete_session(name: str = "last", cwd: str | None = None) -> str | None:
    """Delete one saved session, or every session for this folder when name is 'all'.

    Returns a short status phrase, or None when nothing matched.
    """
    if name != "all" and name != "last" and not _safe_name(name):
        return None
    directory = session_dir(cwd)
    if name == "all":
        paths = list(directory.glob("*.json"))
        for path in paths:
            path.unlink(missing_ok=True)
            _invalidate(path)
        if not paths:
            return None
        count = len(paths)
        return f"{count} session" if count == 1 else f"{count} sessions"
    path = directory / f"{name}.json"
    if name == "last" or not path.exists():
        if name != "last":
            return None
        sessions = list_sessions(cwd, limit=1, include_archived=True)
        if not sessions:
            return None
        path = Path(sessions[0]["path"])
    if not path.is_file():
        return None
    path.unlink()
    _invalidate(path)
    return path.stem


def open_session(name: str, cwd: str | None = None) -> dict | None:
    """Load messages plus the resolved file stem. ``last`` is the newest file."""
    path = _existing_path(name, cwd)
    if path is None:
        return None
    data = _read_payload(path)
    if data is None:
        return None
    messages = data.get("messages")
    if not isinstance(messages, list):
        return None
    return {
        "name": path.stem,
        "path": str(path),
        "model": data.get("model") or "?",
        "messages": messages,
    }


def load_session(name: str, cwd: str | None = None) -> list[dict] | None:
    """Load a session by stem name; 'last' picks the most recent one."""
    opened = open_session(name, cwd)
    if opened is None:
        return None
    return opened["messages"]


def rename_session(name: str, title: str, cwd: str | None = None) -> str | None:
    """Persist a user title. Returns the stored title, or None if it did not match."""
    if not str(title or "").strip():
        return None
    path = _existing_path(name, cwd)
    if path is None:
        return None
    data = _read_payload(path)
    if data is None:
        return None
    data["title"] = trim_title(str(title))
    data["title_custom"] = True
    _write_payload(path, data)
    return str(data["title"])


def toggle_session_flag(
    name: str, flag: str, cwd: str | None = None
) -> tuple[str, bool] | None:
    """Flip ``pinned`` or ``archived``. Returns ``(stem, new_value)``."""
    if flag not in ("pinned", "archived"):
        return None
    path = _existing_path(name, cwd)
    if path is None:
        return None
    data = _read_payload(path)
    if data is None:
        return None
    value = not bool(data.get(flag))
    data[flag] = value
    _write_payload(path, data)
    return path.stem, value


def session_exists(name: str, cwd: str | None = None) -> bool:
    if not _safe_name(name):
        return False
    return (session_dir(cwd) / f"{name}.json").is_file()


@dataclass(frozen=True)
class SessionRowModel:
    """One sidebar or ``/sessions`` line. Headers are not selectable."""

    kind: str
    name: str = ""
    title: str = ""
    primary: str = ""
    secondary: str = ""
    tooltip: str = ""
    active: bool = False
    pinned: bool = False
    archived: bool = False
    continuation: bool = False


def _haystack(record: dict, now: datetime) -> str:
    when = record.get("when")
    day = day_heading(when, now) if isinstance(when, datetime) else ""
    clock = clock_label(when) if isinstance(when, datetime) else ""
    parts = [
        str(record.get("title") or ""),
        str(record.get("preview") or ""),
        str(record.get("model") or ""),
        str(record.get("name") or ""),
        day,
        clock,
        "pinned" if record.get("pinned") else "",
        "archived" if record.get("archived") else "",
    ]
    return " ".join(parts).lower()


def _meta_line(record: dict) -> str:
    when = record.get("when")
    clock = clock_label(when) if isinstance(when, datetime) else "—"
    count = int(record.get("message_count") or 0)
    unit = "msg" if count == 1 else "msgs"
    bits = [clock, f"{count} {unit}", short_model(record.get("model"))]
    if record.get("archived"):
        bits.append("archived")
    return " · ".join(bits)


def _tooltip(record: dict) -> str:
    when = record.get("when")
    exact = exact_label(when) if isinstance(when, datetime) else "unknown time"
    count = int(record.get("message_count") or 0)
    lines = [
        str(record.get("title") or "untitled"),
        exact,
        f"{count} messages · {record.get('model') or '?'}",
        str(record.get("name") or ""),
    ]
    if record.get("pinned"):
        lines.append("pinned")
    if record.get("archived"):
        lines.append("archived")
    if record.get("title_custom"):
        lines.append("renamed")
    return "\n".join(lines)


def _marker(record: dict, active: str | None) -> str:
    if active and record.get("name") == active:
        return "● "
    if record.get("pinned"):
        return "★ "
    return ""


def _session_row(
    record: dict,
    *,
    active: str | None,
    continuation: bool,
    title_suffix: str = "",
) -> SessionRowModel:
    meta = _meta_line(record)
    mark = _marker(record, active)
    title = str(record.get("title") or "untitled")
    if continuation:
        primary = f"{mark}· {meta}" if mark else f"· {meta}"
        secondary = ""
    else:
        primary = f"{mark}{title}{title_suffix}"
        secondary = meta
    return SessionRowModel(
        kind="session",
        name=str(record.get("name") or ""),
        title=title,
        primary=primary,
        secondary=secondary,
        tooltip=_tooltip(record),
        active=bool(active and record.get("name") == active),
        pinned=bool(record.get("pinned")),
        archived=bool(record.get("archived")),
        continuation=continuation,
    )


def _runs(records: list[dict]) -> list[list[dict]]:
    runs: list[list[dict]] = []
    for record in records:
        title = str(record.get("title") or "untitled")
        if runs and str(runs[-1][0].get("title") or "untitled") == title:
            runs[-1].append(record)
        else:
            runs.append([record])
    return runs


def build_session_rows(
    sessions: list[dict],
    *,
    now: datetime | None = None,
    active: str | None = None,
    query: str = "",
    include_archived: bool = False,
) -> list[SessionRowModel]:
    """Group sessions by day, with pinned items first.

    Identical titles in a row share one title line. Later copies keep the
    time, message count, and model so each file can still be opened, without
    repeating the title on every line. ``query`` matches title, preview,
    model, id, and the day label. Archived sessions stay hidden until the
    query matches them or ``include_archived`` is set.
    """
    clock = _naive(now or session_clock())
    needle = " ".join(query.split()).lower()
    visible: list[dict] = []
    for record in sessions:
        archived = bool(record.get("archived"))
        if archived and not include_archived and not needle:
            continue
        if needle and needle not in _haystack(record, clock):
            continue
        visible.append(record)
    visible.sort(
        key=lambda record: (
            record.get("when") is not None,
            record.get("when") or datetime.min,
            str(record.get("name") or ""),
        ),
        reverse=True,
    )

    pinned = [record for record in visible if record.get("pinned")]
    rest = [record for record in visible if not record.get("pinned")]
    groups: list[tuple[str, list[dict]]] = []
    if pinned:
        groups.append(("Pinned", pinned))
    day_buckets: dict[str, list[dict]] = {}
    day_order: list[str] = []
    for record in rest:
        when = record.get("when")
        heading = day_heading(when, clock) if isinstance(when, datetime) else "Undated"
        if heading not in day_buckets:
            day_order.append(heading)
            day_buckets[heading] = []
        day_buckets[heading].append(record)
    for heading in day_order:
        groups.append((heading, day_buckets[heading]))

    rows: list[SessionRowModel] = []
    for heading, records in groups:
        rows.append(
            SessionRowModel(
                kind="header",
                primary=f"{heading} · {len(records)}",
                title=heading,
            )
        )
        for run in _runs(records):
            suffix = f"  ×{len(run)}" if len(run) > 1 else ""
            rows.append(
                _session_row(
                    run[0],
                    active=active,
                    continuation=False,
                    title_suffix=suffix,
                )
            )
            for record in run[1:]:
                rows.append(
                    _session_row(record, active=active, continuation=True)
                )
    return rows


def page_session_rows(
    rows: list[SessionRowModel], offset: int, page_size: int = SESSION_PAGE
) -> tuple[list[SessionRowModel], int]:
    """Return the next render window and the offset after it."""
    size = page_size if page_size > 0 else 1
    start = max(0, offset)
    chunk = rows[start : start + size]
    return chunk, start + len(chunk)


def format_session_listing(
    sessions: list[dict],
    *,
    now: datetime | None = None,
    active: str | None = None,
    query: str = "",
    include_archived: bool = False,
    limit: int = 40,
) -> str:
    """Plain-text session list for ``/sessions`` and ``/search``."""
    rows = build_session_rows(
        sessions,
        now=now,
        active=active,
        query=query,
        include_archived=include_archived,
    )
    if not rows:
        if query.strip():
            return f"(no sessions match {query.strip()!r})"
        return "(no saved sessions)"

    shown = 0
    omitted = 0
    lines: list[str] = []
    pending_header: str | None = None
    session_total = sum(1 for row in rows if row.kind == "session")
    cap = session_total if limit < 0 else max(0, limit)
    for row in rows:
        if row.kind == "header":
            pending_header = row.primary
            continue
        if shown >= cap:
            omitted += 1
            pending_header = None
            continue
        if pending_header:
            lines.append(pending_header)
            pending_header = None
        if row.continuation:
            lines.append(f"{row.primary}  {row.name}".rstrip())
        else:
            lines.append(row.primary)
            detail = "  ".join(part for part in (row.secondary, row.name) if part)
            if detail:
                lines.append(f"  {detail}")
        shown += 1
    if omitted:
        lines.append(f"… {omitted} more — /search to narrow")
    return "\n".join(lines)
