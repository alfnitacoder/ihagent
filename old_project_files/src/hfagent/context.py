"""Cheap context-window estimates and history compaction.

Token counts are a characters/4 estimate so a long session can be trimmed
without another model call. Compaction keeps the system prompt and the newest
turns, and never splits an assistant tool call from its tool results.
"""

from __future__ import annotations

from typing import Any

DEFAULT_CONTEXT_TOKENS = 65_536
_CHARS_PER_TOKEN = 4
SUMMARY_MARK = "summarized to fit the context window"


def estimate_text_tokens(text: str) -> int:
    """Rough token count. Four characters per token is enough to stay under a window."""
    if not text:
        return 0
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def estimate_message_tokens(message: dict[str, Any]) -> int:
    total = 4  # role and framing
    content = message.get("content")
    if isinstance(content, str):
        total += estimate_text_tokens(content)
    elif content is not None:
        total += estimate_text_tokens(str(content))
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            total += estimate_text_tokens(str(call))
            continue
        fn = call.get("function") or {}
        if isinstance(fn, dict):
            total += estimate_text_tokens(str(fn.get("name") or ""))
            total += estimate_text_tokens(str(fn.get("arguments") or ""))
        total += 4
    if message.get("tool_call_id"):
        total += 4
    return total


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(m) for m in messages) + 2


def completion_budget(context_tokens: int) -> int:
    """Prompt budget, leaving room for the model's reply."""
    tokens = int(context_tokens or DEFAULT_CONTEXT_TOKENS)
    if tokens < 256:
        tokens = 256
    reserve = min(8192, max(128, tokens // 8))
    return max(128, tokens - reserve)


def compact_messages(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    protect_tail: int = 0,
    force: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
    """Drop or summarize older turns until the estimate fits ``max_tokens``.

    ``protect_tail`` messages (the current exchange) stay in order. Tool-call
    groups are kept or dropped together. Returns ``(messages, None)`` when
    nothing changed.
    """
    if not messages:
        return messages, None
    before = estimate_messages_tokens(messages)
    if not force and before <= max_tokens:
        return messages, None

    protect_tail = max(0, min(protect_tail, len(messages)))
    head = list(messages[:-protect_tail] if protect_tail else messages)
    tail = list(messages[-protect_tail:] if protect_tail else [])

    system: list[dict[str, Any]] = []
    while head and head[0].get("role") == "system":
        system.append(head.pop(0))

    summary_budget = _summary_budget(max_tokens)
    tail = _shrink_tool_results(
        tail,
        max(64, max_tokens - estimate_messages_tokens(system) - summary_budget),
    )
    available = (
        max_tokens
        - estimate_messages_tokens(system)
        - estimate_messages_tokens(tail)
        - summary_budget
    )
    groups = _groups(head)
    kept, dropped = _select_groups(groups, max(0, available))
    if force and not dropped and groups:
        dropped = [groups[0]]
        kept = groups[1:]
        # A forced drop can leave a newer group that no longer fits; re-check.
        kept, extra = _select_groups(kept, max(0, available))
        dropped.extend(extra)

    if not dropped and tail == (messages[-protect_tail:] if protect_tail else []):
        if not force or before <= max_tokens:
            return messages, None

    summary = _render_summary(dropped, summary_budget) if dropped else ""
    updated: list[dict[str, Any]] = list(system)
    if summary:
        updated.append({"role": "user", "content": summary})
    for group in kept:
        updated.extend(group)
    updated.extend(tail)

    if estimate_messages_tokens(updated) > max_tokens and tail:
        room = max_tokens - estimate_messages_tokens(updated[:-len(tail)])
        updated = updated[:-len(tail)] + _shrink_tool_results(tail, max(64, room))

    info = {
        "dropped_messages": sum(len(group) for group in dropped),
        "before_tokens": before,
        "after_tokens": estimate_messages_tokens(updated),
        "tail_len": len(tail),
    }
    if updated == messages:
        return messages, None
    return updated, info


def _summary_budget(max_tokens: int) -> int:
    budget = min(1200, max(80, max_tokens // 5))
    if budget >= max_tokens:
        budget = max(40, max_tokens // 4)
    return budget


def _groups(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        msg = messages[index]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            group = [msg]
            index += 1
            while index < len(messages) and messages[index].get("role") == "tool":
                group.append(messages[index])
                index += 1
            groups.append(group)
            continue
        groups.append([msg])
        index += 1
    return groups


def _orphan_tool(group: list[dict[str, Any]]) -> bool:
    return bool(group) and all(msg.get("role") == "tool" for msg in group)


def _select_groups(
    groups: list[list[dict[str, Any]]],
    available: int,
) -> tuple[list[list[dict[str, Any]]], list[list[dict[str, Any]]]]:
    """Keep the newest groups that fit. Older groups, and any that do not fit, drop."""
    kept_rev: list[list[dict[str, Any]]] = []
    dropped_rev: list[list[dict[str, Any]]] = []
    used = 0
    stop = False
    for group in reversed(groups):
        if stop or _orphan_tool(group):
            dropped_rev.append(group)
            if not _orphan_tool(group):
                stop = True
            continue
        cost = estimate_messages_tokens(group)
        if used + cost <= available:
            kept_rev.append(group)
            used += cost
        else:
            dropped_rev.append(group)
            stop = True
    kept_rev.reverse()
    dropped_rev.reverse()
    return kept_rev, dropped_rev


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _render_summary(groups: list[list[dict[str, Any]]], budget_tokens: int) -> str:
    lines = [
        "[Context summary — older turns were "
        f"{SUMMARY_MARK}. This is memory, not a new request.]",
        "",
    ]
    for group in groups:
        for msg in group:
            role = str(msg.get("role") or "message")
            if role == "assistant" and msg.get("tool_calls"):
                names = []
                for call in msg["tool_calls"]:
                    fn = call.get("function") if isinstance(call, dict) else None
                    name = ""
                    if isinstance(fn, dict):
                        name = str(fn.get("name") or "")
                    if name:
                        names.append(name)
                called = ", ".join(names) if names else "a tool"
                lines.append(f"- assistant called {called}")
                body = msg.get("content") or ""
                if isinstance(body, str) and body.strip():
                    lines.append(f"- assistant: {_clip(body, 240)}")
                continue
            body = msg.get("content")
            text = body if isinstance(body, str) else ""
            if role == "tool":
                lines.append(f"- tool result: {_clip(text, 240)}")
            else:
                lines.append(f"- {role}: {_clip(text, 400)}")
    text = "\n".join(lines).strip() + "\n"
    char_cap = max(80, budget_tokens * _CHARS_PER_TOKEN)
    if len(text) > char_cap:
        text = text[: char_cap - 20].rstrip() + "\n… [summary truncated]\n"
    return text


def _shrink_tool_results(
    messages: list[dict[str, Any]],
    budget: int,
) -> list[dict[str, Any]]:
    if estimate_messages_tokens(messages) <= budget:
        return messages
    shrunk = [dict(msg) for msg in messages]
    guard = 0
    while estimate_messages_tokens(shrunk) > budget and guard < 12:
        guard += 1
        index = max(
            range(len(shrunk)),
            key=lambda i: len(shrunk[i].get("content") or "")
            if shrunk[i].get("role") == "tool"
            else -1,
        )
        content = shrunk[index].get("content") or ""
        if shrunk[index].get("role") != "tool" or not isinstance(content, str):
            break
        if len(content) < 80:
            break
        keep = max(80, len(content) // 2)
        shrunk[index]["content"] = content[:keep] + "\n… [truncated for context]"
    return shrunk
