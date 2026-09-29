"""Shared slash-command handling for the REPL and the TUI."""

from __future__ import annotations

from typing import Literal

from .agent import Agent, AgentUI
from .config import (
    env_name_for_api_key,
    list_model_names,
    resolve_model_choice,
    save_api_key,
    save_selected_model,
)
from .memory import clear_memory, load_memory
from .sessions import list_sessions, load_session
from .skills import skills_index

HELP = """\
/help             show this help
/tools            list available tools
/models           list models on the current host
/approval [mode]  switch approval mode: auto or default
/memory           show what the agent remembers long-term
/skills           list coding skills (builtin + ~/.hfagent/skills)
/forget           wipe long-term memory
/model <name|#| > switch model (name, unique part, or list number)
/model_api=<key>  paste an Ollama Cloud or Hugging Face key (saved, not shown)
/sessions         list saved sessions
/resume [name]    load a saved session ('last' if no name given)
/clear            reset the conversation
/exit, /quit      leave the agent

TUI keys: f2 choose model · drag to copy · ctrl+shift+c copy · ctrl+b sidebar · ctrl+l clear · ctrl+q quit · up/down history
While a task runs: type a note and it is picked up on the next step. Type stop to cancel.
Classic line REPL: ihagent --console
"""

SlashResult = Literal["exit", "clear", "resume", "handled"]


def pasted_api_key(line: str) -> tuple[bool, str]:
    """Detect `/model_api=<key>` or `/model_api <key>`. The key is never logged."""
    raw = line.strip()
    lower = raw.lower()
    for name in ("/model_api", "/apikey"):
        if lower == name or lower.startswith(name + "=") or lower.startswith(name + " "):
            rest = raw[len(name) :].lstrip()
            if rest.startswith("="):
                rest = rest[1:].strip()
            return True, rest.strip().strip("'").strip('"')
    return False, ""


def dispatch_slash(agent: Agent, ui: AgentUI, line: str) -> SlashResult:
    """Run a `/command`. Unknown commands print help and count as handled."""
    is_key, key = pasted_api_key(line)
    if is_key:
        _apply_pasted_key(agent, ui, key)
        return "handled"

    command, *rest = line.split(maxsplit=1)
    arg = rest[0] if rest else ""

    if command in ("/exit", "/quit"):
        return "exit"

    if command == "/help":
        ui.on_system(HELP)
        return "handled"

    if command == "/approval":
        if arg in ("auto", "default"):
            agent.config.approval = arg
            ui.on_status(f"approval mode: {arg}")
        else:
            ui.on_status(
                f"approval mode: {agent.config.approval} "
                "(usage: /approval auto|default)"
            )
        return "handled"

    if command == "/memory":
        memory = load_memory().strip()
        ui.on_system(memory if memory else "(memory is empty)")
        return "handled"

    if command == "/skills":
        ui.on_system(skills_index())
        return "handled"

    if command == "/forget":
        ui.on_system(clear_memory())
        return "handled"

    if command == "/models":
        names = list_model_names(agent.config.base_url, agent.config.api_key)
        if not names:
            ui.on_system("(no models found on this host)")
        else:
            lines = [
                f"{i}. {'→ ' if name == agent.config.model else '  '}{name}"
                for i, name in enumerate(names, 1)
            ]
            lines.append("switch with /model <number or name>, or f2 in the TUI")
            ui.on_system("\n".join(lines))
        return "handled"

    if command == "/tools":
        ui.on_status(", ".join(agent.registry.names()))
        return "handled"

    if command == "/clear":
        agent.reset()
        ui.on_status("conversation cleared")
        return "clear"

    if command == "/sessions":
        sessions = list_sessions()
        if not sessions:
            ui.on_system("(no saved sessions)")
        else:
            lines = [
                f"{s['name']}  [{s['model'].split('/')[-1]}]  {s['preview']}"
                for s in sessions
            ]
            ui.on_system("\n".join(lines))
        return "handled"

    if command == "/resume":
        name = arg or "last"
        messages = load_session(name)
        if not messages:
            ui.on_error(f"session not found: {name}")
            return "handled"
        agent.set_messages(messages)
        ui.on_status(f"resumed session: {name} ({len(messages)} messages)")
        return "resume"

    if command == "/model":
        if arg:
            names = list_model_names(agent.config.base_url, agent.config.api_key)
            try:
                chosen = resolve_model_choice(arg, names)
            except ValueError as exc:
                ui.on_error(str(exc))
                return "handled"
            agent.config.model = chosen
            save_selected_model(chosen)
        ui.on_status(f"model: {agent.config.model}")
        return "handled"

    ui.on_system(f"unknown command {command}\n{HELP}")
    return "handled"


def _apply_pasted_key(agent: Agent, ui: AgentUI, key: str) -> None:
    import os

    if not key or any(ch.isspace() for ch in key) or len(key) < 8:
        ui.on_status("paste the key as /model_api=...")
        return
    os.environ[env_name_for_api_key(agent.config.base_url)] = key
    agent.config.api_key = key
    agent.client.api_key = key
    save_api_key(key, agent.config.base_url)
    ui.on_status("api key saved")
