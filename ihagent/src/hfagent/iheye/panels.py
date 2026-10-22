"""Hover chat, approval card, and completion-summary rules.

Pure decisions so the AppKit helper and the tests share one policy.
Nothing here imports AppKit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

HOVER_HIDE_DELAY = 0.35
SUMMARY_SECONDS = 8.0
SUMMARY_FOOTER = "Click to open ihagent"

# The card used to keep a fixed head (summary) or tail (reply) and paint that
# one slice. Lines past the slice were discarded, and nothing moved the window,
# so a long status sat clipped. These drive a window that advances over time.
SCROLL_LINES_PER_SEC = 0.75
SCROLL_HOLD = 1.35
SCROLL_MAX_LINES = 24
_PANEL_FONT = 12.0

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


def chars_that_fit(width: float, font_size: float = _PANEL_FONT) -> int:
    """How many characters a line can hold before the card clips the rest."""
    char = max(1.0, float(font_size) * 0.62)
    return max(8, int(max(0.0, float(width)) / char))


def lines_that_fit(height: float, line_height: float = 20.0) -> int:
    """Whole lines inside a text rect, leaving padding so the last one is not cut."""
    usable = max(0.0, float(height) - 2.0)
    return max(1, int(usable / max(1.0, float(line_height))))


def wrap_panel_lines(text: object, *, wrap: int = 36, limit: int = 400) -> list[str]:
    """Wrap to the card width and keep every line, up to a safety cap.

    ``clip_text`` stops at a fixed head or tail. This does not: the caller
    chooses which slice to show, and can move that slice later.
    """
    raw = text if isinstance(text, str) else str(text or "")
    clean = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not clean:
        return []
    if len(clean) > 4000:
        clean = clean[:4000]
    source: list[str] = []
    for line in clean.split("\n"):
        line = _plain_line(line)
        if not line:
            if source and source[-1] != "":
                source.append("")
            continue
        source.append(line)
    wrapped: list[str] = []
    wrap = max(8, int(wrap))
    limit = max(1, int(limit))
    for line in source:
        if len(wrapped) >= limit:
            break
        if line == "":
            if wrapped and wrapped[-1] != "":
                wrapped.append("")
            continue
        while len(line) > wrap:
            if len(wrapped) >= limit:
                line = ""
                break
            cut = line.rfind(" ", 0, wrap + 1)
            if cut < 8:
                cut = wrap
            wrapped.append(line[:cut].strip())
            line = line[cut:].strip()
        if line and len(wrapped) < limit:
            wrapped.append(line)
    while wrapped and wrapped[-1] == "":
        wrapped.pop()
    return wrapped


def scrollable_body(
    text: object,
    *,
    tail: bool = False,
    max_lines: int = SCROLL_MAX_LINES,
    wrap: int | None = None,
) -> str:
    """Card payload. Markup stripped, long enough for the panel to scroll.

    A summary keeps the start of the answer. A live reply keeps the newest
    lines. Either way the overlay receives more than one frozen screen.
    """
    if wrap is None:
        wrap = chars_that_fit(DROP_W - 24.0)
    lines = wrap_panel_lines(text, wrap=wrap, limit=max(max_lines + 8, 80))
    if not lines:
        return ""
    if len(lines) > max_lines:
        if tail:
            lines = lines[-max_lines:]
            if lines and not lines[0].startswith("…"):
                lines[0] = "… " + lines[0].lstrip()
        else:
            lines = lines[:max_lines]
            if lines and not lines[-1].endswith("…"):
                lines[-1] = lines[-1].rstrip() + "…"
    return "\n".join(lines).strip()


def visible_lines(lines: list[str], offset: float, visible: int) -> list[str]:
    """The lines on screen when the window starts at ``offset``.

    Offset 0 is the old fixed slice: the last lines never appear. Callers
    advance ``offset`` so later lines scroll into view.
    """
    if not lines:
        return []
    visible = max(1, int(visible))
    if len(lines) <= visible:
        return list(lines)
    start = int(math.floor(max(0.0, float(offset))))
    start = min(start, len(lines) - visible)
    return list(lines[start : start + visible])


def step_text_scroll(
    offset: float,
    hold: float,
    line_count: int,
    visible: int,
    dt: float,
    *,
    paused: bool = False,
    lines_per_sec: float = SCROLL_LINES_PER_SEC,
    hold_seconds: float = SCROLL_HOLD,
) -> tuple[float, float]:
    """Advance the window. Returns ``(offset, hold)``.

    The window rests at the first line, scrolls, rests on the last line,
    then starts again. ``paused`` (the pointer is on the card) freezes it
    so the line the reader is on does not move. Text that already fits
    stays at offset 0.
    """
    span = max(0, int(line_count) - max(1, int(visible)))
    if span <= 0 or dt < 0 or lines_per_sec <= 0:
        return 0.0, 0.0
    offset = min(max(0.0, float(offset)), float(span))
    hold = max(0.0, float(hold))
    if paused:
        return offset, hold
    dt = float(dt)
    at_start = offset <= 0.001
    at_end = offset >= span - 0.001
    if at_end and hold >= hold_seconds:
        return 0.0, 0.0
    if (at_start or at_end) and hold < hold_seconds:
        hold += dt
        if hold < hold_seconds:
            return (0.0 if at_start else float(span)), hold
        if at_end:
            return 0.0, 0.0
        extra = hold - hold_seconds
        moved = min(span, max(extra * lines_per_sec, 0.02))
        return moved, 0.0
    moved = min(span, offset + lines_per_sec * dt)
    if moved >= span:
        return float(span), 0.0
    return moved, 0.0


def scroll_continues(previous: object, identity: tuple) -> bool:
    """True when the card only grew, so the scroll window should stay put.

    A streaming reply is appended every tick. Resetting the offset on each
    append would pin the card to the first lines for the whole reply.
    """
    if not isinstance(previous, tuple) or len(previous) != len(identity) or len(identity) < 4:
        return False
    if previous[0] != identity[0] or previous[3] != identity[3]:
        return False
    return str(identity[1] or "").startswith(str(previous[1] or "")) and str(
        identity[2] or ""
    ).startswith(str(previous[2] or ""))


def scroll_cycle_seconds(
    line_count: int,
    visible: int,
    *,
    lines_per_sec: float = SCROLL_LINES_PER_SEC,
    hold_seconds: float = SCROLL_HOLD,
) -> float:
    """Seconds for one pass from the first line to the last, including both rests."""
    span = max(0, int(line_count) - max(1, int(visible)))
    if span <= 0 or lines_per_sec <= 0:
        return 0.0
    return hold_seconds + (span / lines_per_sec) + hold_seconds


def page_metrics(
    text: object,
    *,
    width: float,
    height: float,
    footer: str = "",
) -> tuple[list[str], list[str], int]:
    """Wrapped body, footer lines, and how many body lines the rect can show."""
    wrap = chars_that_fit(width)
    lines = wrap_panel_lines(text, wrap=wrap)
    footer_lines = wrap_panel_lines(footer, wrap=wrap) if str(footer or "").strip() else []
    slots = lines_that_fit(height)
    if footer_lines:
        slots = max(1, slots - len(footer_lines))
    return lines, footer_lines, slots


def page_text(
    text: object,
    *,
    width: float,
    height: float,
    offset: float = 0.0,
    footer: str = "",
) -> str:
    """The string the card paints at this scroll offset. The footer stays put."""
    lines, footer_lines, slots = page_metrics(text, width=width, height=height, footer=footer)
    shown = visible_lines(lines, offset, slots)
    return "\n".join([*shown, *footer_lines]).strip()


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


def summary_hold_for(text: object) -> float:
    """Keep a long completion card up long enough to scroll through once."""
    width, height = drop_size("summary")
    rect = layout_drop("summary", width, height).get("reply")
    if rect is None:
        return SUMMARY_SECONDS
    lines, _footer, slots = page_metrics(
        text, width=rect.w, height=rect.h, footer=SUMMARY_FOOTER
    )
    cycle = scroll_cycle_seconds(len(lines), slots)
    return max(SUMMARY_SECONDS, cycle + 0.5)


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
