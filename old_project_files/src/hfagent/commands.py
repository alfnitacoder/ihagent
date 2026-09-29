"""Shared slash-command handling for the REPL and the TUI."""

from __future__ import annotations

from typing import Literal

from .agent import Agent, AgentUI
from .config import list_model_names, resolve_model_choice, save_selected_model
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
/sessions         list saved sessions
/resume [name]    load a saved session ('last' if no name given)
/clear            reset the conversation
/exit, /quit      leave the agent

TUI keys: f2 choose model · drag to copy · ctrl+shift+c copy · ctrl+b sidebar · ctrl+l clear · ctrl+q quit · up/down history
While a task runs: type a note and it is picked up on the next step. Type stop to cancel.
Classic line REPL: ihagent --console
"""

SlashResult = Literal["exit", "clear", "resume", "handled"]


def dispatch_slash(agent: Agent, ui: AgentUI, line: str) -> SlashResult:
    """Run a `/command`. Unknown commands print help and count as handled."""
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
