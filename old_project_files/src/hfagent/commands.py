"""Shared slash-command handling for the REPL and the TUI."""

from __future__ import annotations

from typing import Literal

from .agent import Agent, AgentUI
from .config import (
    ANTHROPIC_BASE_URL,
    ANTHROPIC_DEFAULT_MODEL,
    OLLAMA_BASE_URL,
    api_key_info,
    env_file_has_key,
    env_name_for_api_key,
    format_config_report,
    host_label,
    host_model_env,
    list_model_names,
    normalize_base_url,
    probe_base_url,
    project_env_path,
    resolve_model_choice,
    save_api_key,
    save_base_url,
    save_host_model,
    save_selected_model,
    save_user_setting,
)
from .memory import clear_memory, load_memory
from .sessions import delete_session, list_sessions, load_session
from .skills import skills_index

HELP = """\
/help             show this help
/tools            list available tools
/models           list models on the current host
/approval [mode]  switch approval mode: auto or default
/memory           show what the agent remembers long-term
/skills           list coding skills (builtin + ~/.hfagent/skills)
/forget           wipe long-term memory
/cloud            use Ollama Cloud (saved for the next launch)
/local            use Ollama on this PC (saved for the next launch)
/claude           use Anthropic (Claude) — needs ANTHROPIC_API_KEY
/url <base_url>   test a host (GET /v1/models) and switch to it
/primary          switch back from the fallback host
/config           show effective settings and where each came from
/model <name|#| > switch model (name, unique part, or list number)
/model_api=<key>  paste an Ollama Cloud or Hugging Face key (saved, not shown)
/sessions         list saved sessions
/delete [name]    delete a saved session ('last' if no name, or 'all')
/resume [name]    load a saved session ('last' if no name given)
/clear            reset the conversation
/exit, /quit      leave the agent

TUI keys: /cloud or /local · f2 choose model · drag to copy · ctrl+shift+c copy · ctrl+b sidebar · ctrl+l clear · ctrl+q quit · up/down history
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

    if command == "/delete":
        removed = delete_session(arg or "last")
        if removed is None:
            ui.on_error("session not found")
        else:
            ui.on_status(f"deleted {removed}")
        return "handled"

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

    if command in ("/cloud", "/local"):
        _switch_host(agent, ui, command[1:])
        return "handled"

    if command == "/claude":
        _switch_to_anthropic(agent, ui)
        return "handled"

    if command == "/config":
        ui.on_system(format_config_report(agent.config))
        return "handled"

    if command == "/url":
        _switch_url(agent, ui, arg)
        return "handled"

    if command == "/primary":
        _switch_to_primary(agent, ui)
        return "handled"

    if command == "/host":
        if arg.strip().lower() in ("anthropic", "claude"):
            _switch_to_anthropic(agent, ui)
        else:
            _switch_host(agent, ui, arg)
        return "handled"

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
            agent.config.sources["model"] = "project .env"
        ui.on_status(f"model: {agent.config.model}")
        return "handled"

    ui.on_system(f"unknown command {command}\n{HELP}")
    return "handled"


CLOUD_BASE_URL = "https://ollama.com/v1"


def _switch_host(agent: Agent, ui: AgentUI, kind: str) -> None:
    """Move between Ollama Cloud and local Ollama, and remember both models."""
    import os

    kind = kind.strip().lower()
    if kind not in ("cloud", "local"):
        label = host_label(agent.config.base_url)
        ui.on_status(f"host: {label} · model: {agent.config.model}")
        ui.on_system("switch with /cloud or /local, then f2 to pick a model")
        return

    current = host_label(agent.config.base_url)
    if current in ("cloud", "local") and agent.config.model:
        os.environ[host_model_env(current)] = agent.config.model
        save_host_model(current, agent.config.model)

    base = CLOUD_BASE_URL if kind == "cloud" else OLLAMA_BASE_URL
    remembered = os.environ.get(host_model_env(kind), "").strip()
    key, key_var = api_key_info(base) if kind == "cloud" else ("", "")
    found = bool(remembered)
    model = remembered
    if not model:
        names = list_model_names(base, key)
        if names:
            found = True
            model = names[0]
        else:
            model = agent.config.model
    agent.retarget(base_url=base, api_key=key, model=model, api_key_var=key_var or None)
    os.environ["HF_BASE_URL"] = base
    os.environ["HFAGENT_MODEL"] = model
    save_base_url(base)
    save_selected_model(model)
    agent.config.sources["base_url"] = "project .env"
    agent.config.sources["model"] = "project .env"
    if kind == "cloud" and not key:
        ui.on_status("host: cloud — paste your key as /model_api=...")
    elif not found:
        ui.on_status(f"host: {kind} — press f2 to choose a model")
    else:
        ui.on_status(f"host: {kind} · model: {model}")


def _switch_to_anthropic(agent: Agent, ui: AgentUI) -> None:
    """Point the agent at Anthropic's OpenAI-compatible endpoint (Claude)."""
    import os

    base = ANTHROPIC_BASE_URL
    key, key_var = api_key_info(base)
    model = os.environ.get("HFAGENT_MODEL", "")
    if "claude" not in model.lower():
        model = ANTHROPIC_DEFAULT_MODEL
    agent.retarget(base_url=base, api_key=key, model=model, api_key_var=key_var)
    os.environ["HF_BASE_URL"] = base
    os.environ["HFAGENT_MODEL"] = model
    save_base_url(base)
    save_selected_model(model)
    if not key:
        ui.on_status("host: claude — paste your Anthropic key as /model_api=...")
    else:
        ui.on_status(f"host: claude · model: {model} — press f2 to pick another")


def _switch_url(agent: Agent, ui: AgentUI, raw: str) -> None:
    """GET /v1/models, then point the live session at the URL and save it."""
    import os

    try:
        base = normalize_base_url(raw)
    except ValueError as exc:
        ui.on_error(str(exc))
        return
    key, key_var = api_key_info(base)
    ok, detail, names, _kind = probe_base_url(base, key)
    if not ok:
        ui.on_error(detail)
        return
    current = agent.config.model
    if names and current in names:
        listed = f"model {current} is on this host"
    elif names:
        listed = f"model {current} is not in the list — /model to pick one"
    else:
        listed = f"keeping model {current}"
    ui.on_system(f"{detail}\n{listed}")
    agent.retarget(base_url=base, api_key=key, model=current, api_key_var=key_var)
    agent.on_fallback = False
    os.environ["HF_BASE_URL"] = base
    saved = _persist_base_url(base)
    where = ", ".join(saved)
    # The user file is always written. If the project file was updated too,
    # that file wins on the next launch.
    agent.config.sources["base_url"] = (
        "project .env" if len(saved) > 1 else "~/.hfagent/.env"
    )
    ui.on_status(f"host: {host_label(base)} · {base} — saved HF_BASE_URL to {where}")


def _persist_base_url(url: str) -> list[str]:
    """Write HF_BASE_URL to the user env file.

    When the project .env also sets HF_BASE_URL, update that line so it does
    not shadow the user file (project .env is higher precedence).
    """
    saved = [str(save_user_setting("HF_BASE_URL", url))]
    project = project_env_path()
    if env_file_has_key(project, "HF_BASE_URL"):
        saved.append(str(save_base_url(url, project)))
    return saved


def _switch_to_primary(agent: Agent, ui: AgentUI) -> None:
    """Return to the host we left when the fallback took over."""
    target = agent.primary_target
    if not agent.on_fallback or not target:
        ui.on_status(
            f"already on the primary host · {agent.config.base_url}"
        )
        return
    base, key, model, key_var = target
    ok, detail, _names, kind = probe_base_url(base, key)
    if not ok and kind in {"dns", "tls", "timeout", "tunnel", "connect", "gateway"}:
        ui.on_error(f"primary still unreachable: {detail}")
        return
    agent.retarget(
        base_url=base,
        api_key=key,
        model=model,
        api_key_var=key_var or None,
    )
    agent.on_fallback = False
    agent.config.sources["base_url"] = "primary"
    ui.on_status(f"switched back to primary {base} · model {model}")
    if not ok:
        ui.on_system(detail)


def _apply_pasted_key(agent: Agent, ui: AgentUI, key: str) -> None:
    import os

    if not key or any(ch.isspace() for ch in key) or len(key) < 8:
        ui.on_status("paste the key as /model_api=...")
        return
    var = env_name_for_api_key(agent.config.base_url)
    os.environ[var] = key
    agent.config.api_key = key
    agent.config.api_key_var = var
    agent.client.api_key = key
    save_api_key(key, agent.config.base_url)
    ui.on_status("api key saved")
