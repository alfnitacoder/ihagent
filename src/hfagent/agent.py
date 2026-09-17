"""The agent loop: Thought -> Action -> Observation.

Course mapping (Hugging Face AI Agents Course, Unit 1):
  Thought     = streamed assistant text that accompanies reasoning
  Action      = tool_calls (native JSON function calling)
  Observation = role:"tool" messages appended back into the conversation
"""

from __future__ import annotations

import json
import os
import platform
import re
from typing import Any

from openai import OpenAI

from .config import Config
from .memory import load_memory
from .prompts import SYSTEM_PROMPT
from .tools import ToolRegistry


SHORT_ANNOUNCE_RE = re.compile(
    r"^\s*(?:let's|i'll|i will|let me|i am going to|we'll|we will)\b",
    re.IGNORECASE,
)
MAX_ANNOUNCE_CHARS = 240

AUTO_INTENT_RE = re.compile(
    r"(?:^|\n|[.,;:]\s)(?:i'll|i will|let's|let me|i am going to|we'll|we will)\b",
    re.IGNORECASE,
)

AUTO_CONTINUE_NUDGE = (
    "Continue the task with your tools if it is not complete - try another "
    "approach on failure (longer timeout, background mode, different tool). "
    "If the task IS complete, reply with a detailed final summary. Either "
    "way, do not end on an announcement."
)

PROPOSE_PATTERN = re.compile(
    r"```(?:sh|bash|shell|console|terminal)\b"
    r"|(?:would you like me to|shall i|want me to|should i)\s+(?:run|execute|list|check|show)"
    r"|(?:^|\n|[.,;:]\s)(?:i'll|i will|let's|let me)\s+[a-z]*\s*"
    r"(?:run|execute|list|check|fetch|find|get|extract|create|edit|write|read|search|try|install|configure|update|upgrade|remove|uninstall|download|clone|scan|stop|restart|deploy|build|compile|test|probe|sniff|capture|enumerate|analyze|dump|query|connect|ping|generate|apply|fix|patch|add|clean)\b"
    r"|this command will\b"
    r"|you can (?:run|use) (?:the|this|it|`)"
    r"|here(?:'s| is) (?:the|a) (?:command|file|diff)",
    re.IGNORECASE,
)

DEAD_LOOP_RE = re.compile(
    r"can(?:not|'?t)\s+(?:execute|run)\s+commands"
    r"|i (?:have no|don't have) (?:access to|ability to) (?:execute|run)",
    re.IGNORECASE,
)

FABRICATED_RESULT_RE = re.compile(
    r"<tool_response>.*?(?:</tool_response>|$)", re.DOTALL
)

FABRICATION_NUDGE = (
    "Never write tool_response tags or invent command output. Call the "
    "tool now and report only its real result."
)

FABRICATION_NUDGE_ESCALATED = (
    "You invented a result again. That is forbidden. Reply with ONLY the "
    "function call, in exactly this JSON shape and nothing else:\n"
    '{"name": "run_command", "arguments": {"command": "<the actual command>"}}\n'
    "Use ssh_run instead of run_command for remote hosts. No prose, no "
    "tool_response tags, no invented output."
)

NUDGE_MESSAGE = (
    "Do not propose commands in text or ask permission. Call the tool now "
    "as a function call - run_command for local, ssh_run for remote hosts - "
    "then report only the real output."
)

PROPOSAL_NUDGE_ESCALATED = (
    "You announced: \"{announcement}\"\n"
    "Do not announce it again. Execute it now. Reply with ONLY the function "
    "call, in exactly this JSON shape and nothing else:\n"
    '{{"name": "ssh_run", "arguments": {{"host": "<host>", '
    '"command": "<the exact command>"}}}}\n'
    "Use run_command instead for local commands. No prose, no plan text."
)

AFFIRMATION_RE = re.compile(
    r"^\s*(?:yes|y|yeah|yep|ok|okay|sure|go|go ahead|do it|run it|please)\W*$",
    re.IGNORECASE,
)

TEXT_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>|<tool>\s*(\{.*?\})\s*</tool>",
    re.DOTALL,
)
FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_text_tool_calls(content: str) -> tuple[list[dict], str]:
    """Extract tool calls embedded in plain text (e.g. '<tool_call>{...}</tool_call>').

    Some servers (Ollama with GGUF templates, bare ReAct models) return the
    Action as text instead of a structured tool_calls field. Handles
    '<tool_call>{...}</tool_call>', '<tool>{...}</tool>' and fenced
    '```json {"name": ..., "arguments": ...} ``` blocks. Returns
    (tool_calls, remaining_content).
    """
    calls: list[dict] = []

    def _capture(match: re.Match) -> str:
        raw = match.group(1) or match.group(2) or match.group(0)
        raw = raw.strip().removeprefix("```").removesuffix("```").strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return ""
        name = payload.get("name")
        args = payload.get("arguments", payload.get("args", {}))
        if isinstance(name, str) and name:
            calls.append(
                {
                    "id": f"text_{len(calls)}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args if isinstance(args, dict) else {}),
                    },
                }
            )
        return ""  # strip the tag block from visible content

    remaining = TEXT_TOOL_CALL_RE.sub(_capture, content)
    if not calls:
        remaining = FENCED_JSON_RE.sub(_capture, content)
    if not calls:
        calls, remaining = _bare_json_calls(content)
    return calls, remaining.strip()


def _bare_json_calls(content: str) -> tuple[list[dict], str]:
    """Detect bare '{"name": ..., "arguments": ...}' objects in text.

    Some models emit the tool-call JSON without any wrapper. Uses incremental
    decoding so nested argument objects parse correctly, and removes only the
    matched spans.
    """
    decoder = json.JSONDecoder()
    calls: list[dict] = []
    kept: list[str] = []
    idx = 0
    while True:
        pos = content.find('{"name"', idx)
        if pos == -1:
            break
        try:
            payload, end = decoder.raw_decode(content, pos)
        except json.JSONDecodeError:
            idx = pos + 1
            continue
        if isinstance(payload, dict) and isinstance(payload.get("name"), str):
            args = payload.get("arguments", payload.get("args", {}))
            calls.append(
                {
                    "id": f"text_{len(calls)}",
                    "type": "function",
                    "function": {
                        "name": payload["name"],
                        "arguments": json.dumps(
                            args if isinstance(args, dict) else {}
                        ),
                    },
                }
            )
            kept.append(content[idx:pos])
            idx = pos + end
        else:
            idx = pos + 1
    kept.append(content[idx:])
    return calls, "".join(kept)


def system_prompt() -> str:
    """System prompt with live environment context appended."""
    home = os.path.expanduser("~")
    env = (
        f"Environment: {platform.system()} {platform.release()}, "
        f"working directory: {os.getcwd()}, home directory: {home}."
    )
    parts = [env, SYSTEM_PROMPT]
    memory = load_memory().strip()
    if memory:
        parts.append(
            "Persistent memory from previous sessions (hosts, preferences, "
            "context - trust this):\n" + memory
        )
    return "\n\n".join(parts)


class AgentUI:
    """Interface between the agent loop and the presentation layer."""

    def on_status(self, text: str) -> None: ...
    def on_assistant_delta(self, text: str) -> None: ...
    def on_assistant_done(self, text: str) -> None: ...
    def on_action(self, tool_name: str, arguments: dict) -> None: ...
    def on_observation(self, tool_name: str, result: str) -> None: ...
    def on_error(self, message: str) -> None: ...

    def approve(
        self, tool_name: str, arguments: dict, preview: str | None = None
    ) -> bool:
        return False


class Agent:
    def __init__(self, config: Config, registry: ToolRegistry, ui: AgentUI):
        self.config = config
        self.registry = registry
        self.ui = ui
        self.client = OpenAI(
            api_key=config.api_key or "none",
            base_url=config.base_url,
            timeout=120,
            max_retries=2,
        )
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt()}
        ]

    # ------------------------------------------------------------------ state

    def set_messages(self, history: list[dict[str, Any]]) -> None:
        """Replace conversation history, keeping the fresh system prompt."""
        self.messages = [
            m
            for m in self.messages
            if m.get("role") == "system"
        ] + [m for m in history if m.get("role") != "system"]

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": system_prompt()}]

    # ------------------------------------------------------------------- loop

    def run(self, user_input: str) -> str:
        rollback_marker = len(self.messages)
        if AFFIRMATION_RE.match(user_input.strip()):
            last_assistant = next(
                (
                    m
                    for m in reversed(self.messages)
                    if m.get("role") == "assistant"
                ),
                None,
            )
            if last_assistant and PROPOSE_PATTERN.search(
                last_assistant.get("content") or ""
            ):
                user_input = (
                    user_input.strip()
                    + " - do it yourself now with a tool call (run_command "
                    "or ssh_run). No text answer, no invented output."
                )
        self.messages.append({"role": "user", "content": user_input})
        nudges = {"fabrication": 0, "proposal": 0}
        tool_used = False
        auto = self.config.approval == "auto"

        def roll_back(reason: str) -> str:
            """Discard the poisoned exchange so history stays clean."""
            del self.messages[rollback_marker:]
            self.ui.on_error(reason)
            return ""
        try:
            for _step in range(self.config.max_steps):
                message = self._step()
                if message.get("tool_calls"):
                    tool_used = True
                    continue
                content = message.get("content") or ""
                # Fabrication must be detected on the RAW content, before
                # stripping (the cleaned text never contains the tag).
                fabricated = (
                    "<tool_response" in content
                    or "</tool_response" in content
                )
                if fabricated:
                    # Always strip faked results. NOTE: Ollama rejects
                    # assistant messages with null content, so use "".
                    content = FABRICATED_RESULT_RE.sub("", content)
                    # orphan tags from half-fabricated blocks
                    content = re.sub(r"</?tool_response>?", "", content)
                    content = content.strip()
                    message["content"] = content
                if DEAD_LOOP_RE.search(content):
                    return roll_back(
                        "model refuses to use its tools; rolled back this "
                        "exchange - try rephrasing or /clear"
                    )
                intent = bool(PROPOSE_PATTERN.search(content))
                if auto and not intent:
                    # in auto mode ANY first-person intent phrase counts as
                    # "not finished" - verb matching is too leaky
                    intent = bool(AUTO_INTENT_RE.search(content))
                proposing = intent or (
                    not tool_used
                    and len(content) <= MAX_ANNOUNCE_CHARS
                    and bool(SHORT_ANNOUNCE_RE.match(content))
                )
                fab_limit = 4 if self.config.approval == "auto" else 2
                if (
                    fabricated
                    and content == ""
                    and nudges["fabrication"] >= fab_limit
                ):
                    return roll_back(
                        "model kept fabricating results; rolled back this "
                        "exchange"
                    )
                if fabricated and nudges["fabrication"] < fab_limit:
                    escalate = nudges["fabrication"] >= 1
                    nudges["fabrication"] += 1
                    self.ui.on_status(
                        "model faked a tool result - nudging it to run tools"
                    )
                    self.messages.append(
                        {
                            "role": "user",
                            "content": FABRICATION_NUDGE_ESCALATED
                            if escalate
                            else FABRICATION_NUDGE,
                        }
                    )
                    continue
                if proposing:
                    proposal_limit = 6 if auto else 2
                    if nudges["proposal"] < proposal_limit:
                        escalate = nudges["proposal"] >= 1
                        nudges["proposal"] += 1
                        self.ui.on_status(
                            "model announced without acting - nudging it"
                            + (" (auto)" if auto else "")
                        )
                        if escalate and auto:
                            nudge = AUTO_CONTINUE_NUDGE
                        elif escalate:
                            nudge = PROPOSAL_NUDGE_ESCALATED.format(
                                announcement=content.strip()[:300]
                            )
                        else:
                            nudge = NUDGE_MESSAGE
                        self.messages.append(
                            {"role": "user", "content": nudge}
                        )
                        continue
                    if auto:
                        # never stall on a plan in auto mode: return the plan
                        # as the final answer instead of waiting for 'run it'
                        self.ui.on_status(
                            "model kept announcing without acting after "
                            "multiple nudges - returning its plan"
                        )
                        return content
                    if not tool_used:
                        return roll_back(
                            "model kept announcing steps without acting; "
                            "rolled back this exchange. Tip: phrase it as a "
                            "direct command, e.g. 'on kali219 run: "
                            "ls /usr/share/wordlists'"
                        )
                    # default mode mid-task: keep completed work, hand back
                    self.ui.on_status(
                        "model stopped after announcing next steps - "
                        "say 'run it' to continue"
                    )
                    return content
                if not content and tool_used:
                    return "(done - results in the observations above)"
                return content
        except KeyboardInterrupt:
            self._patch_interrupted()
            self.ui.on_error("interrupted by user")
            return ""
        self.ui.on_error("reached max steps without a final answer")
        return ""

    # ------------------------------------------------------------- internals

    @staticmethod
    def _sanitize_messages(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Ollama 400s on assistant messages with null content and no
        tool_calls; scrub any that slipped in (e.g. from saved sessions)."""
        cleaned = []
        for m in messages:
            if (
                m.get("role") == "assistant"
                and m.get("content") is None
                and not m.get("tool_calls")
            ):
                m = {**m, "content": ""}
            cleaned.append(m)
        return cleaned

    def _step(self) -> dict[str, Any]:
        """One model turn: stream text + tool calls, then run pending tools."""
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, str]] = {}

        stream = self.client.chat.completions.create(
            model=self.config.model,
            messages=self._sanitize_messages(self.messages),
            tools=self.registry.specs(),
            stream=True,
            temperature=self.config.temperature,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
                self.ui.on_assistant_delta(delta.content)
            for tc in delta.tool_calls or []:
                idx = tc.index if tc.index is not None else 0
                slot = tool_calls.setdefault(
                    idx, {"id": "", "name": "", "arguments": ""}
                )
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments

        content = "".join(content_parts)

        ordered = [tool_calls[i] for i in sorted(tool_calls)]
        if not ordered and content:
            parsed, remaining = parse_text_tool_calls(content)
            valid = [
                {
                    "id": c["id"],
                    "name": c["function"]["name"],
                    "arguments": c["function"]["arguments"],
                }
                for c in parsed
                if self.registry.get(c["function"]["name"])
            ]
            if valid:
                ordered = valid
                content = remaining
        self.ui.on_assistant_done(content)

        # NOTE: never store None here - Ollama rejects assistant messages with
        # null content when they carry no tool_calls; empty string is safe.
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if ordered:
            message["tool_calls"] = [
                {
                    "id": call["id"] or f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": call["arguments"] or "{}",
                    },
                }
                for i, call in enumerate(ordered)
            ]
        self.messages.append(message)

        if not message.get("tool_calls"):
            return message

        for call in message["tool_calls"]:
            result = self._execute(call)
            self.messages.append(
                {"role": "tool", "tool_call_id": call["id"], "content": result}
            )
        return message

    def _execute(self, call: dict[str, Any]) -> str:
        name = call["function"]["name"]
        raw_args = call["function"]["arguments"]
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError as exc:
            return f"Error: invalid JSON arguments for {name}: {exc}"
        if not isinstance(args, dict):
            return f"Error: arguments for {name} must be a JSON object"

        tool = self.registry.get(name)
        if tool is None:
            available = ", ".join(self.registry.names())
            return f"Error: unknown tool '{name}'. Available tools: {available}"

        self.ui.on_action(name, args)
        if tool.needs_approval and not self._approved(tool, args):
            return "User declined this action. Propose an alternative or ask why."

        if name == "ssh_run" and isinstance(args.get("host"), str):
            self.ui.on_status(
                f"remote task on {args['host']}: executing, waiting for "
                "completion..."
            )
        try:
            result = tool.run(**args)
        except Exception as exc:  # surfaced to the model as an Observation
            result = f"Error: {type(exc).__name__}: {exc}"
        result = self._truncate(result)
        self.ui.on_observation(name, result)
        return result

    def _approved(self, tool, args: dict) -> bool:
        if self.config.approval == "auto":
            return True
        try:
            preview = tool.preview(**args)
        except Exception:
            preview = None
        return self.ui.approve(tool.name, args, preview)

    def _truncate(self, text: str) -> str:
        limit = self.config.max_tool_result_chars
        if len(text) > limit:
            return text[:limit] + "\n... [truncated]"
        return text

    def _patch_interrupted(self) -> None:
        """Close dangling tool calls so the next turn stays API-valid."""
        if not self.messages:
            return
        last = self.messages[-1]
        if last.get("role") != "assistant" or not last.get("tool_calls"):
            return
        answered = {
            m["tool_call_id"] for m in self.messages if m.get("role") == "tool"
        }
        for call in last["tool_calls"]:
            if call["id"] not in answered:
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": "[interrupted by user]",
                    }
                )
