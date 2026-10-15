"""Hover chat, approval card, and completion-summary rules.

Pure decisions so the AppKit helper and the tests share one policy.
Nothing here imports AppKit.
"""

from __future__ import annotations

from dataclasses import dataclass

HOVER_HIDE_DELAY = 0.35
SUMMARY_SECONDS = 8.0

DROP_W = 280.0


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float


def clean_chat_text(text: object, limit: int = 8000) -> str:
    """One user message from the hover field. Empty means do not send."""
    raw = text if isinstance(text, str) else str(text or "")
    raw = raw.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(raw) > limit:
        raw = raw[:limit]
    return raw


def _plain_line(line: str) -> str:
    line = line.replace("**", "").replace("__", "").replace("`", "")
    if line.startswith("#"):
        line = line.lstrip("#").strip()
    return line.strip()


def clip_text(
    text: object,
    *,
    max_lines: int = 4,
    max_chars: int = 220,
    wrap: int = 68,
    tail: bool = False,
) -> str:
    """Trim a reply to a few short lines. No model call, no markup."""
    raw = text if isinstance(text, str) else str(text or "")
    clean = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not clean:
        return ""
    if len(clean) > 4000:
        clean = clean[-4000:] if tail else clean[:4000]
    source: list[str] = []
    for line in clean.split("\n"):
        line = _plain_line(line)
        if not line:
            if source and source[-1] != "":
                source.append("")
            continue
        source.append(line)
    wrapped: list[str] = []
    hidden = False
    cap = max(max_lines, 24) if tail else max_lines
    for index, line in enumerate(source):
        if hidden:
            break
        if line == "":
            if wrapped and wrapped[-1] != "":
                wrapped.append("")
            continue
        while len(line) > wrap:
            if len(wrapped) >= cap:
                hidden = True
                line = ""
                break
            cut = line.rfind(" ", 0, wrap)
            if cut < 24:
                cut = wrap
            wrapped.append(line[:cut].strip())
            line = line[cut:].strip()
        if line:
            if len(wrapped) >= cap:
                hidden = True
            else:
                wrapped.append(line)
        if not tail and index < len(source) - 1 and len(wrapped) >= max_lines:
            hidden = True
    while wrapped and wrapped[-1] == "":
        wrapped.pop()
    if tail and len(wrapped) > max_lines:
        hidden = True
        wrapped = wrapped[-max_lines:]
    body = "\n".join(wrapped).strip()
    if len(body) > max_chars:
        body = body[: max_chars - 1].rstrip() + "…"
        hidden = True
    if hidden and body and not body.endswith("…"):
        body += "…"
    return body


def trim_reply(text: object) -> str:
    """Latest reply for the hover panel: the newest lines, capped."""
    return clip_text(text, max_lines=6, max_chars=360, tail=True)


def summarize_answer(text: object) -> str:
    """First lines of a finished answer. Cheap, local, no extra model call."""
    return clip_text(text, max_lines=4, max_chars=220, tail=False)


def approval_body(tool: object, preview: object) -> str:
    name = str(tool or "").strip() or "action"
    body = clip_text(preview, max_lines=3, max_chars=160, tail=False)
    if body:
        return f"Approval needed: {name}\n{body}"
    return f"Approval needed: {name}"


def approval_answer(label: object) -> bool | None:
    """y accepts, n/N declines. Anything else is not a button."""
    text = str(label or "").strip().lower()
    if text in {"y", "yes"}:
        return True
    if text in {"n", "no"}:
        return False
    return None


def drop_size(mode: str) -> tuple[float, float]:
    if mode == "chat-approval":
        return DROP_W, 220.0
    if mode == "chat":
        return DROP_W, 176.0
    if mode == "approval":
        return DROP_W, 128.0
    if mode == "summary":
        return DROP_W, 112.0
    return DROP_W, 176.0


def drop_contents(*, hover: bool, approval: bool, summary: bool) -> str:
    """What the card under the eye is showing. Closed means no card."""
    if hover and approval:
        return "chat-approval"
    if hover:
        return "chat"
    if approval:
        return "approval"
    if summary:
        return "summary"
    return "closed"


def layout_drop(mode: str, width: float, height: float) -> dict[str, Rect]:
    """Subview frames. Origin is the bottom-left, matching AppKit."""
    if mode == "closed":
        return {}
    if mode == "summary":
        return {"reply": Rect(12, 10, width - 24, height - 20)}
    if mode == "approval":
        return {
            "yes": Rect(10, 10, 72, 26),
            "no": Rect(90, 10, 72, 26),
            "reply": Rect(10, 44, width - 20, height - 54),
        }
    if mode == "chat-approval":
        return {
            "yes": Rect(10, 10, 72, 26),
            "no": Rect(90, 10, 72, 26),
            **_chat_controls(width, height, field_y=42, reply_y=90),
        }
    return _chat_controls(width, height, field_y=10, reply_y=58)


def _chat_controls(
    width: float,
    height: float,
    *,
    field_y: float,
    reply_y: float,
) -> dict[str, Rect]:
    """Field, the 👁 look toggle, a capture note, and the reply."""
    look_w = 62.0
    return {
        "field": Rect(10, field_y, max(40.0, width - 24 - look_w), 26),
        "look": Rect(width - 10 - look_w, field_y, look_w, 26),
        "note": Rect(10, field_y + 30, width - 20, 14),
        "reply": Rect(10, reply_y, width - 20, max(20.0, height - reply_y - 8)),
    }


def step_hover(
    *,
    open_now: bool,
    inside: bool,
    focused: bool,
    away_for: float,
    suppressed: bool,
    dt: float,
    delay: float = HOVER_HIDE_DELAY,
) -> tuple[bool, float, bool]:
    """Return ``(open, away_for, suppressed)``.

    Esc sets ``suppressed``. The card stays shut until the pointer leaves
    and comes back. Leaving without focus starts the short close delay.
    """
    if suppressed:
        if not inside and not focused:
            return False, 0.0, False
        return False, 0.0, True
    if inside or focused:
        return True, 0.0, False
    if not open_now:
        return False, 0.0, False
    away = away_for + max(0.0, dt)
    if away >= delay:
        return False, 0.0, False
    return True, away, False


def step_summary_timer(
    remaining: float,
    hovered: bool,
    dt: float,
    showing: bool,
) -> tuple[float, bool]:
    """Count down only while the pointer is elsewhere. Hover pauses the clock."""
    if not showing:
        return remaining, False
    if hovered:
        return remaining, True
    remaining = remaining - max(0.0, dt)
    if remaining <= 0:
        return 0.0, False
    return remaining, True


def summary_should_show(
    *,
    enabled: bool,
    text: object,
    remaining: float,
    hovered: bool,
) -> bool:
    if not enabled or not str(text or "").strip():
        return False
    if hovered and remaining > 0:
        return True
    return remaining > 0


def should_restore_front(
    front_pid: int,
    our_pid: int,
    previous_pid: int | None,
) -> bool:
    """Give the keyboard back only if this helper actually became frontmost."""
    if not previous_pid or previous_pid == our_pid:
        return False
    return front_pid == our_pid


def pointer_inside(
    px: float,
    py: float,
    rects: list[tuple[float, float, float, float]],
    pad: float = 8.0,
) -> bool:
    for x, y, w, h in rects:
        if (x - pad) <= px <= (x + w + pad) and (y - pad) <= py <= (y + h + pad):
            return True
    return False
