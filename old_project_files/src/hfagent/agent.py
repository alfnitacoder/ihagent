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
import queue
from pathlib import Path
import re
from typing import Any

from openai import OpenAI

from .config import Config, api_key_info, normalize_base_url
from .context import compact_messages, completion_budget
from .errors import (
    classify_api_failure,
    explain_failure,
    is_connection_failure,
    is_context_length_error,
    kind_label,
)
from .memory import load_memory
from .prompts import SYSTEM_PROMPT
from .reasoning import ThinkSplitter, reasoning_text
from .runtime import infer_device
from .skills import format_skills_for_prompt
from .tools import ToolRegistry


SHORT_ANNOUNCE_RE = re.compile(
    r"^\s*(?:let's|i'll|i will|i['’]m going to|i am going to|i['’]m gonna|"
    r"let me|we['’]ll|we will|next i(?:['’]ll| will)|proceeding to|about to)\b",
    re.IGNORECASE,
)
MAX_ANNOUNCE_CHARS = 240

# Mid-task chat. Exact phrases only, so "don't stop yet" is extra info.
STOP_MESSAGE_RE = re.compile(
    r"^(?:/stop|stop|stop task|stop the task|cancel|abort|halt)[.!]?$",
    re.IGNORECASE,
)


def is_stop_message(text: str) -> bool:
    return bool(STOP_MESSAGE_RE.match(text.strip()))

# First-person future/intent openers. Used in --approval auto so GGUFs that
# narrate "I will …" / "I'm going to …" without a tool call get nudged hard.
AUTO_INTENT_RE = re.compile(
    r"(?:^|\n|[.,;:]\s)"
    r"(?:i'll|i will|i['’]m going to|i am going to|i['’]m gonna|gonna|"
    r"let's|let me|we['’]ll|we will|next i(?:['’]ll| will)|"
    r"i need to|proceeding to|about to)\b",
    re.IGNORECASE,
)

AUTO_CONTINUE_NUDGE = (
    "Continue the task with your tools if it is not complete - try another "
    "approach on failure (longer timeout, background mode, different tool). "
    "If the task IS complete, reply with a detailed final summary. Either "
    "way, do not end on an announcement."
)

# Omit vague words (start/call/open/continue/increase) so prose like
# "Let's start with…" does not nudge. "I will increase…" stays auto-only
# via AUTO_INTENT_RE.
_ACTION_VERBS = (
    r"run|execute|list|check|fetch|find|get|extract|create|edit|write|read|"
    r"search|try|install|configure|update|upgrade|remove|uninstall|download|"
    r"clone|scan|stop|restart|deploy|build|compile|test|probe|sniff|capture|"
    r"enumerate|analyze|dump|query|connect|ping|generate|apply|fix|patch|"
    r"add|clean|retry|invoke|launch|ssh|reconnect|tail|kill|curl|wget|nmap|"
    r"apt|pip|brew"
)

PROPOSE_PATTERN = re.compile(
    r"```(?:sh|bash|shell|console|terminal|jsx|javascript|js|tsx|ts|css|html|python|py)\b"
    r"|####?\s+\S+\.(?:js|jsx|ts|tsx|css|py|html|json)\b"
    r"|(?:would you like me to|shall i|want me to|should i)\s+(?:run|execute|list|check|show)"
    r"|(?:^|\n|[.,;:]\s)(?:i'll|i will|i['’]m going to|i am going to|"
    r"let's|let me|next i(?:['’]ll| will)|i need to)\s+[a-z]*\s*"
    rf"(?:{_ACTION_VERBS})\b"
    r"|this command will\b"
    r"|you can (?:run|use) (?:the|this|it|`)"
    r"|here(?:'s| is) (?:the|a) (?:command|file|diff)"
    # remote-host announce without a tool call (common Qwen GGUF stall)
    r"|\b(?:ssh(?:_run)?|ssh(?:ing)?)\b.*\b(?:run|execute|install|scan|check)\b"
    r"|\bon\s+`?[A-Za-z0-9._-]+`?\b[^\n]{0,80}\b(?:run|execute|install|"
    r"scan|check|apt|nmap)\b",
    re.IGNORECASE,
)

FILE_DUMP_RE = re.compile(
    r"```(?:jsx|javascript|js|tsx|ts|css|html|python|py)\b"
    r"|####?\s+\S+\.(?:js|jsx|ts|tsx|css|py|html)\b",
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

TOOL_CHOICE_HINT_RE = re.compile(
    r"enable-auto-tool-choice|tool-call-parser|auto tool choice",
    re.IGNORECASE,
)
ERROR_FIELD_RE = re.compile(
    r"""['"](?:error|message)['"]\s*:\s*['"]([^'"]+)['"]"""
)


def format_api_error(
    exc: BaseException,
    *,
    base_url: str = "",
    model: str = "",
    key_var: str = "",
) -> str:
    """Turn an OpenAI/vLLM exception into a short line the TUI can wrap.

    Connection, auth, model-not-found, timeout, and server failures name the
    URL and the cause class. Other errors keep the previous short unwrapping.
    """
    raw = str(exc).strip() or type(exc).__name__
    # Prefer the longest useful error/message field (avoid bare keys like "model").
    candidates = [m.group(1).strip() for m in ERROR_FIELD_RE.finditer(raw)]
    candidates = [c for c in candidates if c and c.lower() not in {"error", "message"}]
    inner = max(candidates, key=len) if candidates else raw
    if TOOL_CHOICE_HINT_RE.search(inner) or TOOL_CHOICE_HINT_RE.search(raw):
        return (
            "This model server rejected native tool calls. "
            "Retrying in text-tool mode."
        )
    kind = classify_api_failure(exc)
    if kind:
        return explain_failure(
            kind,
            exc,
            base_url=base_url,
            model=model,
            key_var=key_var,
        )
    if inner.startswith("Error code:") and " - " in inner:
        inner = inner.split(" - ", 1)[1].strip()
    # Common HF / OpenAI shapes that leave a useless short token.
    if inner.lower() in {"model", "error", "invalid"} and len(raw) > len(inner):
        inner = raw
    return inner if len(inner) <= 280 else inner[:277] + "..."


def is_tool_choice_unsupported(exc: BaseException) -> bool:
    text = str(exc)
    return bool(TOOL_CHOICE_HINT_RE.search(text))


def _full_message(obj: Any) -> Any:
    """Return the message of a non-streaming completion, else None."""
    if obj is None or isinstance(obj, (str, bytes, list, tuple)):
        return None
    choices = getattr(obj, "choices", None)
    if not choices:
        return None
    first = choices[0]
    if getattr(first, "message", None) is not None and not hasattr(first, "delta"):
        return first.message
    return None


def _tool_calls_from_message(message_obj: Any) -> list[dict[str, str]]:
    raw = getattr(message_obj, "tool_calls", None)
    if raw is None and isinstance(message_obj, dict):
        raw = message_obj.get("tool_calls")
    calls: list[dict[str, str]] = []
    for index, tc in enumerate(raw or []):
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            name = fn.get("name") if isinstance(fn, dict) else ""
            arguments = fn.get("arguments") if isinstance(fn, dict) else ""
            call_id = tc.get("id") or ""
        else:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", "") if fn is not None else ""
            arguments = getattr(fn, "arguments", "") if fn is not None else ""
            call_id = getattr(tc, "id", "") or ""
        calls.append(
            {
                "id": call_id or f"call_{index}",
                "name": name or "",
                "arguments": arguments or "",
            }
        )
    return calls


def _absorb_tool_delta(tool_calls: dict[int, dict[str, str]], tc: Any) -> None:
    idx = tc.index if getattr(tc, "index", None) is not None else 0
    slot = tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
    if getattr(tc, "id", None):
        slot["id"] = tc.id
    function = getattr(tc, "function", None)
    if function is not None and getattr(function, "name", None):
        slot["name"] = function.name
    if function is not None and getattr(function, "arguments", None):
        slot["arguments"] += function.arguments


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

REASONING_ONLY_NUDGE = (
    "Your previous reply was only reasoning and had no final answer. "
    "Reply now with the final answer as normal text, or call a tool. "
    "Do not stop at reasoning."
)

NUDGE_MESSAGE = (
    "Do not propose commands in text or ask permission. Call the tool now "
    "as a function call - run_command for local, ssh_run for remote hosts - "
    "then report only the real output."
)

PROPOSAL_NUDGE_ESCALATED = (
    "You announced: \"{announcement}\"\n"
    "Do not announce it again. Emit a real tool/function call NOW via the "
    "API tool-calling channel (not as chat prose). Prefer ssh_run for remote "
    "hosts and run_command for local. If your runtime can only emit JSON "
    "text, use exactly:\n"
    '{{"name": "ssh_run", "arguments": {{"host": "<host>", '
    '"command": "<the exact command>"}}}}\n'
    "No plan text, no narration, no permission questions."
)

WRITE_FILE_NUDGE = (
    "You pasted source code in chat instead of writing the file. That does "
    "not change the disk. Call write_file NOW with the complete file "
    "contents. JSON shape:\n"
    '{"name": "write_file", "arguments": {"path": "<path>", '
    '"content": "<full file>"}}\n'
    "Do that for every file you claimed to change. No more fenced dumps, "
    "no invented read_file output."
)

REPEAT_FAIL_HINT = (
    "\nHINT: You already got this exact error. Do NOT call the same tool "
    "with the same arguments again. Call list_dir on \".\" and \"src\", "
    "read package.json / webpack.config.js / vite.config.*, then open or "
    "create the files that actually exist (or that the config requires)."
)

REPEAT_FAIL_NUDGE = (
    "Stop repeating the failed tool call. Discover the real layout first: "
    "list_dir \".\", list_dir \"src\" if it exists, read package.json and "
    "any bundler config. Then improve the files that are there — or "
    "write_file any missing entrypoints the config expects. Never retry "
    "an identical failing read_file/path."
)

REPEAT_SAME_CMD_NUDGE = (
    "Stop repeating the identical run_command — the output is unchanged. "
    "Act on what you already know: fix webpack/babel (babel.config.js, "
    "preset modules), edit source, re-run npm test / curl the page, or "
    "summarize. Do not grep the same log again."
)

POST_TOOL_NUDGE = (
    "Continue now. If you need another tool, emit ONLY this form (no prose):\n"
    "<tool_call>\n"
    "<function=TOOL_NAME>\n"
    "<parameter=ARG_NAME>value</parameter>\n"
    "</function>\n"
    "</tool_call>\n"
    "Otherwise write a short factual summary of the tool results already "
    "shown. Do not say \"I'll check…\" or \"Let me…\" without a tool call."
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
# Qwen / Huihui abliterated style (vLLM may leave these in content):
# <tool_call><function=name><parameter=k>v</parameter></function></tool_call>
QWEN_XML_TOOL_RE = re.compile(
    r"(?:<tool_call>\s*)?<function=([A-Za-z0-9_.-]+)\s*>(.*?)</function>\s*(?:</tool_call>)?",
    re.DOTALL | re.IGNORECASE,
)
QWEN_XML_PARAM_RE = re.compile(
    r"<parameter=([A-Za-z0-9_.-]+)\s*>(.*?)</parameter>",
    re.DOTALL | re.IGNORECASE,
)


def _parse_qwen_xml_tool_calls(content: str) -> tuple[list[dict], str]:
    calls: list[dict] = []

    def _repl(match: re.Match) -> str:
        name = (match.group(1) or "").strip()
        body = match.group(2) or ""
        if not name:
            return ""
        args: dict[str, str] = {}
        for pm in QWEN_XML_PARAM_RE.finditer(body):
            key = (pm.group(1) or "").strip()
            val = (pm.group(2) or "").strip()
            if key:
                args[key] = val
        calls.append(
            {
                "id": f"text_{len(calls)}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args),
                },
            }
        )
        return ""

    remaining = QWEN_XML_TOOL_RE.sub(_repl, content)
    return calls, remaining.strip()


def parse_text_tool_calls(content: str) -> tuple[list[dict], str]:
    """Extract tool calls embedded in plain text (e.g. '<tool_call>{...}</tool_call>').

    Some servers (Ollama with GGUF templates, bare ReAct models) return the
    Action as text instead of a structured tool_calls field. Handles
    '<tool_call>{...}</tool_call>', '<tool>{...}</tool>', Qwen XML
    ``<function=name><parameter=...>`` blocks, and fenced
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
    if not calls:
        calls, remaining = _parse_qwen_xml_tool_calls(content)
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


def _shell_rules() -> str:
    system = platform.system()
    if system == "Windows":
        return (
            "This computer is Windows. run_command uses cmd or PowerShell "
            "on THIS machine only. Do not run macOS or Linux commands "
            "(ifconfig, ip addr, sw_vers, uname, hostname -I, "
            "ipconfig getifaddr). Try one Windows command, read the result, "
            "and stop. Do not repeat the question with another operating "
            "system's command. For this computer's IP addresses, run "
            "exactly: ipconfig"
        )
    if system == "Darwin":
        return (
            "This computer is macOS. run_command uses zsh on THIS machine "
            "only. Do not run Linux or Windows commands (ip addr, "
            "hostname -I, ipconfig /all, Get-NetIPAddress). Try one macOS "
            "command, read the result, and stop. For this computer's IP "
            "addresses, run exactly: ipconfig getifaddr en0"
        )
    return (
        "This computer is Linux. run_command uses bash on THIS machine "
        "only. Do not run macOS or Windows commands (sw_vers, ipconfig, "
        "networksetup). Try one Linux command, read the result, and stop. "
        "For this computer's IP addresses, run exactly: hostname -I"
    )


def system_prompt(base_url: str = "") -> str:
    env = (
        f"Environment: {platform.system()} {platform.release()}, "
        f"cwd={Path.cwd()}"
    )
    parts = [env, _shell_rules(), SYSTEM_PROMPT]
    # Anthropic's content filter rejects the entire request when the system
    # prompt carries flagged material. The user's persistent memory can hold
    # offensive-security notes, so omit it on the Claude host (it stays
    # available for Ollama / HF hosts).
    if "api.anthropic.com" not in base_url:
        memory = load_memory().strip()
        if memory:
            parts.append(
                "Persistent memory from previous sessions (hosts, preferences, "
                "context - trust this):\n" + memory
            )
    skills = format_skills_for_prompt()
    if skills:
        parts.append(skills)
    return "\n\n".join(parts)



def _steer_parts(parts: list) -> list:
    """Mid-task note in front of a look capture that is already a content list."""
    note = (
        "The user paused you mid-task and added this. "
        "Read it, adjust what you are doing, then continue "
        "with your tools. Do not ignore it.\n\n"
    )
    if parts and isinstance(parts[0], dict) and parts[0].get("type") == "text":
        first = dict(parts[0])
        first["text"] = note + str(first.get("text") or "")
        return [first, *parts[1:]]
    return [{"type": "text", "text": note}, *parts]


class AgentUI:
    """Interface between the agent loop and the presentation layer."""

    def on_status(self, text: str) -> None: ...
    def on_assistant_delta(self, text: str) -> None: ...
    def on_assistant_done(self, text: str) -> None: ...
    def on_thought_delta(self, text: str) -> None:
        """Streamed reasoning / chain-of-thought. Dim it; it is not the answer."""

    def on_thought_done(self, text: str) -> None:
        """Reasoning for this step is complete."""
    def on_action(self, tool_name: str, arguments: dict) -> None: ...
    def on_observation(self, tool_name: str, result: str) -> None: ...
    def on_error(self, message: str) -> None: ...
    def on_system(self, text: str) -> None:
        """Long-form info (/help, /memory, /sessions). Defaults to status."""
        self.on_status(text)

    def on_runtime(self, device: str, prompt_tokens: int, completion_tokens: int) -> None:
        """GPU/CPU and token counts for the latest model call."""

    def approve(
        self, tool_name: str, arguments: dict, preview: str | None = None
    ) -> bool:
        return False


class Agent:
    @property
    def ui(self) -> AgentUI:
        return self._ui

    @ui.setter
    def ui(self, value: AgentUI) -> None:
        self._ui = value
        try:
            from .robot_bridge import attach_ui

            attach_ui(value)
        except Exception:
            pass

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
            {"role": "system", "content": system_prompt(config.base_url)}
        ]
        self._native_tools = True
        self._inbox: queue.Queue[str] = queue.Queue()
        self.device = "…"
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self._exchange_start = 0
        self.session_name: str | None = None
        self.on_fallback = False
        # (base_url, api_key, model, api_key_var) captured when we leave primary.
        self.primary_target: tuple[str, str, str, str] | None = None

    def retarget(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        api_key_var: str | None = None,
    ) -> None:
        """Point the next request at another host without restarting."""
        self.config.base_url = base_url
        self.config.api_key = api_key
        self.config.model = model
        if api_key_var:
            self.config.api_key_var = api_key_var
        self.client = OpenAI(
            api_key=api_key or "none",
            base_url=base_url,
            timeout=120,
            max_retries=2,
        )
        # Rebuild the system message for the new host (memory is host-aware:
        # omitted on Claude so its content filter does not reject the turn).
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = {
                "role": "system",
                "content": system_prompt(base_url),
            }
        self.device = "…"

    def steer(self, text: str) -> None:
        """Queue a chat message while a turn is already running."""
        if isinstance(text, list):
            if text:
                self._inbox.put(text)
            return
        cleaned = str(text or "").strip()
        if cleaned:
            self._inbox.put(cleaned)

    def _consume_steer(self) -> str:
        """Fold queued chat into the conversation.

        Returns ``stop``, ``note``, or ``""`` when the inbox was empty.
        """
        kind = ""
        while True:
            try:
                item = self._inbox.get_nowait()
            except queue.Empty:
                return kind
            if isinstance(item, list):
                self.messages.append(
                    {"role": "user", "content": _steer_parts(item)}
                )
                self.ui.on_status("got your message — continuing with it")
                kind = "note"
                continue
            text = str(item or "").strip()
            if not text:
                continue
            if is_stop_message(text):
                self._discard_inbox()
                return "stop"
            self.messages.append(
                {
                    "role": "user",
                    "content": (
                        "The user paused you mid-task and added this. "
                        "Read it, adjust what you are doing, then continue "
                        "with your tools. Do not ignore it.\n\n" + text
                    ),
                }
            )
            self.ui.on_status("got your message — continuing with it")
            kind = "note"

    def _discard_inbox(self) -> None:
        while True:
            try:
                self._inbox.get_nowait()
            except queue.Empty:
                return

    # ------------------------------------------------------------------ state

    def set_messages(self, history: list[dict[str, Any]]) -> None:
        """Replace conversation history, keeping the fresh system prompt."""
        self.messages = [
            m
            for m in self.messages
            if m.get("role") == "system"
        ] + [m for m in history if m.get("role") != "system"]

    def reset(self) -> None:
        self.messages = [
            {"role": "system", "content": system_prompt(self.config.base_url)}
        ]
        self.session_name = None

    # ------------------------------------------------------------------- loop

    def run(self, user_input: str) -> str:
        """Run one turn. iheye shows thinking, then a done pulse."""
        from .iheye import publish, publish_summary

        publish("thinking")
        result = ""
        try:
            result = self._run(user_input)
            return result
        finally:
            try:
                publish_summary(result if isinstance(result, str) else "")
            except Exception:
                pass
            publish("done")

    def _run(self, user_input: str) -> str:
        self._exchange_start = len(self.messages)
        if isinstance(user_input, str) and AFFIRMATION_RE.match(user_input.strip()):
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
        nudges = {"fabrication": 0, "proposal": 0, "empty": 0, "repeat_fail": 0}
        tool_used = False
        auto = self.config.approval == "auto"
        self._repeat_fail = {"sig": None, "n": 0}
        write_targets: dict[str, int] = {}

        def roll_back(reason: str) -> str:
            """Discard the poisoned exchange so history stays clean."""
            del self.messages[self._exchange_start :]
            self.ui.on_error(reason)
            return ""
        def finish(content: str) -> str | None:
            """End the turn, unless the user just added a mid-task note."""
            kind = self._consume_steer()
            if kind == "stop":
                self._patch_interrupted()
                self.ui.on_error("stopped")
                return ""
            if kind == "note":
                return None
            return content

        try:
            for _step in range(self.config.max_steps):
                if self._consume_steer() == "stop":
                    self._patch_interrupted()
                    self.ui.on_error("stopped")
                    return ""
                message = self._step()
                if message.get("tool_calls"):
                    tool_used = True
                    # Interleaved announce→tool turns must not exhaust the
                    # proposal budget for the whole task.
                    nudges["proposal"] = 0
                    nudges["empty"] = 0
                    # Runaway-write guard: a model that keeps rewriting the
                    # same file in one turn (e.g. misreading "open it" as
                    # "keep extending the poem") grows the content each call,
                    # so its tool signature changes every time and the
                    # error/identical-output guards below never fire.
                    for _c in message["tool_calls"]:
                        _fn = _c.get("function", {})
                        if _fn.get("name") in ("write_file", "edit_file"):
                            try:
                                _p = json.loads(
                                    _fn.get("arguments") or "{}"
                                ).get("path", "")
                            except json.JSONDecodeError:
                                _p = ""
                            if _p:
                                write_targets[_p] = write_targets.get(_p, 0) + 1
                    if any(n >= 3 for n in write_targets.values()):
                        self.ui.on_status(
                            "same file rewritten repeatedly - stopping the loop"
                        )
                        done = finish(
                            "Stopped: I kept rewriting the same file without a "
                            "new instruction, so I halted the loop. The file is "
                            "saved as-is. Tell me the specific change you want."
                        )
                        if done is None:
                            write_targets.clear()
                            continue
                        return done
                    if self._repeat_fail.get("n", 0) >= 5:
                        return roll_back(
                            "model kept retrying the same failing tool "
                            "call; rolled back this exchange - try "
                            "'improve the webapp: list files first'"
                        )
                    if self._repeat_fail.get("n", 0) >= 2 and nudges[
                        "repeat_fail"
                    ] < 2:
                        nudges["repeat_fail"] += 1
                        same_ok = (
                            not any(
                                (m.get("content") or "").startswith("Error:")
                                for m in self.messages[-3:]
                                if m.get("role") == "tool"
                            )
                        )
                        self.ui.on_status(
                            "same tool call repeated - nudging to make progress"
                        )
                        self.messages.append(
                            {
                                "role": "user",
                                "content": REPEAT_SAME_CMD_NUDGE
                                if same_ok
                                else REPEAT_FAIL_NUDGE,
                            }
                        )
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
                if not content:
                    reasoning_only = bool(message.get("_reasoning_only"))
                    # After tools, empty replies are common on some servers —
                    # one retry, then finish with observations (don't burn
                    # max_steps / roll back successful work). Reasoning-only
                    # replies are not an answer, so they keep the retry path.
                    if tool_used and not reasoning_only:
                        if nudges["empty"] < 1:
                            nudges["empty"] += 1
                            self.ui.on_status(
                                "model returned an empty response - retrying"
                            )
                            self.messages.append(
                                {
                                    "role": "user",
                                    "content": POST_TOOL_NUDGE,
                                }
                            )
                            continue
                        self.ui.on_status(
                            "empty after tools - finishing with observations"
                        )
                        done = finish("(done - results in the observations above)")
                        if done is None:
                            continue
                        return done
                    if nudges["empty"] < 2:
                        nudges["empty"] += 1
                        if reasoning_only:
                            self.ui.on_status(
                                "model returned reasoning only — asking for a final answer"
                            )
                            nudge = REASONING_ONLY_NUDGE
                        else:
                            self.ui.on_status(
                                "model returned an empty response - retrying"
                            )
                            nudge = (
                                "Your previous response was empty. "
                                "Respond now: either call the "
                                "appropriate tool, or write your full "
                                "answer as text."
                            )
                        self.messages.append({"role": "user", "content": nudge})
                        continue
                    if reasoning_only:
                        return roll_back(
                            "model returned only reasoning and no final answer; "
                            "rolled back this exchange - try rephrasing"
                        )
                    return roll_back(
                        "model returned only empty responses; rolled back "
                        "this exchange - try rephrasing"
                    )
                # Auto mode: soft mid-task stalls ("...", "one moment",
                # "continuing") after real tool use — force another tool call
                # or a real summary instead of hanging on filler.
                soft_stall = bool(
                    auto
                    and tool_used
                    and content
                    and len(content) <= 120
                    and re.search(
                        r"(?:\.{3}|…|one moment|hang on|continuing|"
                        r"stand by|working on it|give me a (?:sec|second|moment))\b",
                        content,
                        re.IGNORECASE,
                    )
                )
                if soft_stall and nudges["proposal"] < 2:
                    nudges["proposal"] += 1
                    self.ui.on_status(
                        "model stalled mid-task - nudging it (auto)"
                    )
                    self.messages.append(
                        {"role": "user", "content": AUTO_CONTINUE_NUDGE}
                    )
                    continue
                if proposing:
                    # Mid-task announces are common with local GGUFs / Qwen XML
                    # tool models. After tools already ran, nudge once with a
                    # short Qwen-friendly prompt, then accept text as the
                    # summary (escalated JSON nudges often yield empty replies).
                    if auto and tool_used:
                        if nudges["proposal"] < 1:
                            nudges["proposal"] += 1
                            self.ui.on_status(
                                "model announced without acting - nudging it (auto)"
                            )
                            self.messages.append(
                                {"role": "user", "content": POST_TOOL_NUDGE}
                            )
                            continue
                        self.ui.on_status(
                            "post-tool announce after nudge - accepting as summary"
                        )
                        done = finish(content)
                        if done is None:
                            continue
                        return done
                    proposal_limit = (10 if tool_used else 6) if auto else 2
                    if nudges["proposal"] < proposal_limit:
                        nudges["proposal"] += 1
                        self.ui.on_status(
                            "model announced without acting - nudging it"
                            + (" (auto)" if auto else "")
                        )
                        # Identical re-announce after a nudge → escalate now.
                        prev_assistant = next(
                            (
                                m.get("content") or ""
                                for m in reversed(self.messages[:-1])
                                if m.get("role") == "assistant"
                                and not m.get("tool_calls")
                            ),
                            "",
                        )
                        repeated = (
                            prev_assistant.strip() == content.strip()
                            and bool(prev_assistant.strip())
                        )
                        if FILE_DUMP_RE.search(content):
                            nudge = WRITE_FILE_NUDGE
                        elif (
                            auto
                            or tool_used
                            or nudges["proposal"] >= 2
                            or repeated
                        ):
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
                        done = finish(content)
                        if done is None:
                            continue
                        return done
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
                    done = finish(content)
                    if done is None:
                        continue
                    return done
                done = finish(content)
                if done is None:
                    continue
                return done
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
        tool_calls; scrub any that slipped in (e.g. from saved sessions).

        Keys starting with ``_`` are local bookkeeping and must not be sent.
        """
        cleaned = []
        for m in messages:
            if any(str(key).startswith("_") for key in m):
                m = {k: v for k, v in m.items() if not str(k).startswith("_")}
            if (
                m.get("role") == "assistant"
                and m.get("content") is None
                and not m.get("tool_calls")
            ):
                m = {**m, "content": ""}
            cleaned.append(m)
        return cleaned

    def _fit_context(self, *, force: bool = False) -> None:
        """Summarize older turns before the request outgrows the window."""
        protect = max(0, len(self.messages) - self._exchange_start)
        budget = completion_budget(self.config.context_tokens)
        if force:
            budget = max(256, budget // 2)
        updated, info = compact_messages(
            self.messages,
            max_tokens=budget,
            protect_tail=protect,
            force=force,
        )
        if info is None:
            return
        self.messages = updated
        tail_len = int(info.get("tail_len", protect))
        self._exchange_start = max(0, len(self.messages) - tail_len)
        self.ui.on_status(
            "context full — summarized "
            f"{info.get('dropped_messages', 0)} older messages "
            f"(~{info.get('after_tokens', 0)} tokens, "
            f"limit {self.config.context_tokens})"
        )

    def _create_completion(
        self,
        *,
        tools: bool,
        include_usage: bool = True,
        context_retry: bool = False,
    ):
        self._fit_context()
        anthropic = "api.anthropic.com" in self.config.base_url
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": self._sanitize_messages(self.messages),
            "stream": True,
        }
        if not anthropic:
            # Recent Claude models (5.x) reject `temperature` as deprecated.
            kwargs["temperature"] = self.config.temperature
        if include_usage:
            kwargs["stream_options"] = {"include_usage": True}
        if tools:
            kwargs["tools"] = self.registry.specs()
        if anthropic:
            # Anthropic's API requires max_tokens; the OpenAI-compat layer has
            # no implicit default, so set a generous cap for agent replies.
            kwargs.setdefault("max_tokens", 8192)
        try:
            return self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if include_usage and "stream_options" in str(exc).lower():
                return self._create_completion(
                    tools=tools,
                    include_usage=False,
                    context_retry=context_retry,
                )
            if not context_retry and is_context_length_error(exc):
                self.ui.on_status(
                    "context limit hit — compacting history and retrying"
                )
                self._fit_context(force=True)
                return self._create_completion(
                    tools=tools,
                    include_usage=include_usage,
                    context_retry=True,
                )
            raise

    def _open_stream_once(self):
        """Stream a turn; fall back if the server cannot do native tools."""
        if self._native_tools:
            try:
                return self._create_completion(tools=True)
            except Exception as exc:
                if not is_tool_choice_unsupported(exc):
                    raise
                self._native_tools = False
                self.ui.on_status(
                    "server rejected native tools — using text tool calls"
                )
        return self._create_completion(tools=False)

    def _try_fallback(self, exc: BaseException) -> bool:
        """Switch to the fallback host on connection failures only."""
        fallback = (self.config.fallback_base_url or "").strip()
        if not fallback or self.on_fallback:
            return False
        if not is_connection_failure(exc):
            return False
        try:
            fallback = normalize_base_url(fallback)
        except ValueError:
            pass
        if fallback.rstrip("/") == self.config.base_url.rstrip("/"):
            return False
        self._activate_fallback(fallback, exc)
        return True

    def _activate_fallback(self, fallback: str, exc: BaseException) -> None:
        model = self.config.fallback_model or self.config.model
        if self.config.fallback_api_key:
            key = self.config.fallback_api_key
            key_var = "HFAGENT_FALLBACK_API_KEY"
        else:
            key, key_var = api_key_info(fallback)
        if not self.on_fallback:
            self.primary_target = (
                self.config.base_url,
                self.config.api_key,
                self.config.model,
                self.config.api_key_var,
            )
        kind = classify_api_failure(exc) or "connect"
        self.on_fallback = True
        self.ui.on_system(
            f"Primary host unreachable ({kind_label(kind)}): {self.config.base_url}\n"
            f"Switched to fallback {fallback} · model {model}.\n"
            "Type /primary to switch back."
        )
        self.ui.on_status("using fallback host — /primary to switch back")
        self.retarget(
            base_url=fallback,
            api_key=key,
            model=model,
            api_key_var=key_var,
        )
        self.config.sources["base_url"] = "HFAGENT_FALLBACK_BASE_URL"
        if self.config.fallback_model:
            self.config.sources["model"] = "HFAGENT_FALLBACK_MODEL"

    def _open_stream(self):
        try:
            stream = self._open_stream_once()
        except Exception as exc:
            if not self._try_fallback(exc):
                raise
            stream = self._open_stream_once()
        return self._retry_stream_on_disconnect(stream)

    def _retry_stream_on_disconnect(self, stream: Any):
        """If the socket dies before any token, try the fallback host once."""
        message = _full_message(stream)
        if message is not None:
            return stream
        try:
            iterator = iter(stream)
        except TypeError:
            return stream
        try:
            first = next(iterator)
        except StopIteration:
            return iter(())
        except Exception as exc:
            if not self._try_fallback(exc):
                raise
            return self._open_stream_once()

        def chained():
            yield first
            yield from iterator

        return chained()

    def _step(self) -> dict[str, Any]:
        """One model turn: stream text + tool calls, then run pending tools."""
        content_parts: list[str] = []
        thought_parts: list[str] = []
        tool_calls: dict[int, dict[str, str]] = {}
        splitter = ThinkSplitter()
        prompt_tokens = 0
        completion_tokens = 0

        def _push_reply(body: str, final: bool = False) -> None:
            try:
                from .iheye import publish_reply

                publish_reply(body, final=final)
            except Exception:
                pass

        def add_answer(text: str) -> None:
            if not text:
                return
            content_parts.append(text)
            self.ui.on_assistant_delta(text)
            _push_reply("".join(content_parts))

        def add_thought(text: str) -> None:
            if not text:
                return
            thought_parts.append(text)
            self.ui.on_thought_delta(text)

        def absorb_content(text: str) -> None:
            answer, thought = splitter.feed(text)
            add_thought(thought)
            add_answer(answer)

        def note_usage(obj: Any) -> None:
            nonlocal prompt_tokens, completion_tokens
            usage = getattr(obj, "usage", None)
            if usage is None and isinstance(obj, dict):
                usage = obj.get("usage")
            if usage is None:
                return
            if isinstance(usage, dict):
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)
                return
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

        def absorb_message(message_obj: Any) -> None:
            add_thought(reasoning_text(message_obj))
            body = getattr(message_obj, "content", None)
            if body is None and isinstance(message_obj, dict):
                body = message_obj.get("content")
            if isinstance(body, str) and body:
                absorb_content(body)
            for index, call in enumerate(_tool_calls_from_message(message_obj)):
                tool_calls[index] = call

        stream = self._open_stream()
        full = _full_message(stream)
        if full is not None:
            note_usage(stream)
            absorb_message(full)
        else:
            for chunk in stream:
                note_usage(chunk)
                choices = getattr(chunk, "choices", None)
                if not choices:
                    add_thought(reasoning_text(chunk))
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is None:
                    embedded = getattr(choice, "message", None)
                    if embedded is not None:
                        absorb_message(embedded)
                    continue
                add_thought(reasoning_text(delta))
                if getattr(delta, "content", None):
                    absorb_content(delta.content)
                for tc in getattr(delta, "tool_calls", None) or []:
                    _absorb_tool_delta(tool_calls, tc)

        tail_answer, tail_thought = splitter.finish()
        add_thought(tail_thought)
        add_answer(tail_answer)

        content = "".join(content_parts)
        thought = "".join(thought_parts).strip()
        if thought and not content.strip():
            content = ""
        if thought:
            self.ui.on_thought_done(thought)
        self._report_runtime(prompt_tokens, completion_tokens)

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
        _push_reply(content, final=True)

        # NOTE: never store None here - Ollama rejects assistant messages with
        # null content when they carry no tool_calls; empty string is safe.
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if thought and not content.strip() and not ordered:
            message["content"] = ""
            message["_reasoning_only"] = True
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

    def _report_runtime(self, prompt_tokens: int, completion_tokens: int) -> None:
        device = infer_device(self.config.base_url, self.config.model)
        if device:
            self.device = device
        if prompt_tokens or completion_tokens:
            self.prompt_tokens = prompt_tokens
            self.completion_tokens = completion_tokens
        self.ui.on_runtime(self.device, self.prompt_tokens, self.completion_tokens)

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
        if self._needs_approval(tool, args) and not self._approved(tool, args):
            result = "User declined this action. Propose an alternative or ask why."
            self.ui.on_observation(name, result)
            return result

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
        sig = (name, json.dumps(args, sort_keys=True, default=str))
        prev = getattr(self, "_repeat_fail", {"sig": None, "n": 0})
        if result.startswith("Error:"):
            if prev.get("sig") == sig:
                prev["n"] = int(prev.get("n") or 0) + 1
            else:
                prev = {"sig": sig, "n": 1}
            self._repeat_fail = prev
            if prev["n"] >= 2 and REPEAT_FAIL_HINT not in result:
                result = result + REPEAT_FAIL_HINT
        elif name == "run_command" and prev.get("sig") == sig and prev.get(
            "out"
        ) == result:
            prev["n"] = int(prev.get("n") or 0) + 1
            self._repeat_fail = prev
            if prev["n"] >= 2 and "identical run_command" not in result:
                result = result + "\nHINT: " + REPEAT_SAME_CMD_NUDGE
        else:
            self._repeat_fail = {"sig": sig, "n": 1, "out": result}
        self.ui.on_observation(name, result)
        return result

    def _needs_approval(self, tool, args: dict) -> bool:
        checker = getattr(tool, "requires_approval", None)
        if not callable(checker):
            return bool(tool.needs_approval)
        try:
            return bool(checker(**args))
        except TypeError:
            return bool(tool.needs_approval)
        except Exception:
            return bool(tool.needs_approval)

    def _approved(self, tool, args: dict) -> bool:
        if self.config.approval == "auto":
            return True
        from .iheye import publish

        publish("waiting")
        try:
            preview = tool.preview(**args)
        except Exception:
            preview = None
        try:
            from .iheye import publish_approval

            publish_approval(tool.name, preview or "")
        except Exception:
            pass
        try:
            return self.ui.approve(tool.name, args, preview)
        finally:
            try:
                from .iheye import clear_approval

                clear_approval()
            except Exception:
                pass
            publish("thinking")

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
