"""Split reasoning channels from the answer the user should see.

Qwen3 (via Ollama) and similar models put chain-of-thought in a `reasoning`
or `reasoning_content` field, or wrap it in `<think>` tags while leaving
`content` empty. Reasoning is thought, not the final answer.
"""

from __future__ import annotations

import re
from typing import Any

_OPEN = "<think>"
_CLOSE = "</think>"
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.IGNORECASE | re.DOTALL)


def extra_text(obj: Any, names: tuple[str, ...]) -> str:
    """Read string fields from a dict, an object, or a pydantic extra bag."""
    if obj is None:
        return ""
    found: list[str] = []
    seen: set[str] = set()

    def take(value: Any) -> None:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            found.append(value)

    if isinstance(obj, dict):
        for name in names:
            take(obj.get(name))
        return "".join(found)

    for name in names:
        take(getattr(obj, name, None))
    for bag_name in ("model_extra", "__pydantic_extra__"):
        bag = getattr(obj, bag_name, None)
        if isinstance(bag, dict):
            for name in names:
                take(bag.get(name))
    return "".join(found)


def reasoning_text(obj: Any) -> str:
    return extra_text(obj, ("reasoning", "reasoning_content"))


class ThinkSplitter:
    """Stream filter that separates `<think>` blocks from the visible answer.

    Tags may arrive split across chunks. Text outside the tags is the answer.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False

    def feed(self, chunk: str) -> tuple[str, str]:
        if not chunk:
            return "", ""
        self._buf += chunk
        answer: list[str] = []
        thought: list[str] = []
        while self._buf:
            if self._in_think:
                close_at = self._buf.lower().find(_CLOSE)
                if close_at == -1:
                    keep = _partial_suffix(self._buf, _CLOSE)
                    emit = self._buf[:-keep] if keep else self._buf
                    self._buf = self._buf[-keep:] if keep else ""
                    if emit:
                        thought.append(emit)
                    break
                thought.append(self._buf[:close_at])
                self._buf = self._buf[close_at + len(_CLOSE) :]
                self._in_think = False
                continue
            open_at = self._buf.lower().find(_OPEN)
            if open_at == -1:
                keep = _partial_suffix(self._buf, _OPEN)
                emit = self._buf[:-keep] if keep else self._buf
                self._buf = self._buf[-keep:] if keep else ""
                if emit:
                    answer.append(emit)
                break
            if open_at:
                answer.append(self._buf[:open_at])
            self._buf = self._buf[open_at + len(_OPEN) :]
            self._in_think = True
        return "".join(answer), "".join(thought)

    def finish(self) -> tuple[str, str]:
        if not self._buf:
            return "", ""
        leftover = self._buf
        self._buf = ""
        if self._in_think:
            self._in_think = False
            return "", leftover
        return leftover, ""


def split_think(content: str) -> tuple[str, str]:
    """Return ``(answer, thought)`` for a complete message body."""
    splitter = ThinkSplitter()
    answer, thought = splitter.feed(content or "")
    tail_answer, tail_thought = splitter.finish()
    return answer + tail_answer, thought + tail_thought


def strip_think(content: str) -> str:
    """Visible answer with `<think>` blocks removed. Used by tests and sanitize."""
    if not content or "<think" not in content.lower():
        return content
    return split_think(content)[0]


def _partial_suffix(buf: str, tag: str) -> int:
    """Length of the suffix of ``buf`` that is a proper prefix of ``tag``."""
    lowered = buf.lower()
    tag = tag.lower()
    max_keep = min(len(buf), len(tag) - 1)
    for size in range(max_keep, 0, -1):
        if tag.startswith(lowered[-size:]):
            return size
    return 0


# Imported by tests that want the pattern without going through the splitter.
THINK_PATTERN = _THINK_RE
