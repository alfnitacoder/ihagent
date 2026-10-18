"""Slash commands for MCP servers."""

from __future__ import annotations

import shlex
from typing import Any

from ..agent import Agent, AgentUI
from .config import (
    NAME_RE,
    PRESET_NAMES,
    ConfigError,
    ServerConfig,
    build_preset,
    config_path,
    is_google_workspace,
    load_servers,
    mcp_disabled,
    parse_server,
    preset_note,
    remove_server,
    token_dir,
    upsert_server,
)
from .session import McpHub

USAGE = """\
usage:
  /mcp                         list servers, status, and tool counts
  /mcp add <name> [preset|url|command...]
  /mcp remove <name>
  /mcp login <name>
  /mcp logout <name>
  /mcp tools [name]
  /mcp reload
  /mcp enable <name>
  /mcp disable <name>

presets: google, filesystem, github, fetch
  /mcp add google
  /mcp add google --client-id ID --client-secret SECRET
  /mcp add filesystem ~/code
  /mcp add github --token ghp_...
  /mcp add fetch
  /mcp add docs https://example.com/mcp
  /mcp add local -- python3 server.py
"""

_GOOGLE_DISABLED = (
    "Google sign-in opens a browser when MCP connections are enabled. "
    "HFAGENT_MCP_DISABLE is set, so the browser was not opened."
)


def handle_mcp(agent: Agent, ui: AgentUI, arg: str) -> None:
    try:
        parts = shlex.split(arg)
    except ValueError as exc:
        ui.on_error(f"could not parse /mcp: {exc}")
        return
    if not parts:
        ui.on_system(_listing(agent))
        return
    action = parts[0].lower()
    rest = parts[1:]
    try:
        if action == "add":
            ui.on_system(_add(agent, rest, ui))
        elif action == "remove":
            ui.on_system(_remove(agent, rest))
        elif action == "login":
            ui.on_system(_login(agent, rest, ui))
        elif action == "logout":
            ui.on_system(_logout(agent, rest))
        elif action == "tools":
            ui.on_system(_tools(agent, rest))
        elif action == "reload":
            ui.on_system(_reload(agent))
        elif action in {"enable", "disable"}:
            ui.on_system(_set_enabled(agent, action, rest))
        elif action in {"help", "-h", "--help"}:
            ui.on_system(USAGE)
        else:
            ui.on_system(USAGE)
    except ConfigError as exc:
        ui.on_error(str(exc))
    except Exception as exc:
        ui.on_error(f"mcp: {type(exc).__name__}: {exc}")


def hub_for(agent: Agent) -> McpHub:
    current = getattr(agent, "mcp", None)
    if isinstance(current, McpHub):
        return current
    hub = McpHub(agent.registry, lambda: agent.ui)
    agent.mcp = hub
    return hub


def _listing(agent: Agent) -> str:
    path = config_path()
    try:
        servers = load_servers(path)
    except ConfigError as exc:
        return str(exc)
    lines = [f"MCP config: {path}", f"tokens: {token_dir()}"]
    if mcp_disabled():
        lines.append("MCP connections are off (HFAGENT_MCP_DISABLE).")
    if not servers:
        lines.append("no servers. /mcp add google|filesystem|github|fetch, or /mcp add <name> <url|command>")
        return "\n".join(lines)
    live = {state.name: state for state in hub_for(agent).states()}
    for name, cfg in servers.items():
        state = live.get(name)
        if state is None:
            status = "disabled" if not cfg.enabled else "not connected"
            detail = ""
            count = "0 tools"
        else:
            status = state.status
            detail = f" — {state.detail}" if state.detail else ""
            if state.server_tool_count and state.server_tool_count != state.tool_count:
                count = f"{state.tool_count} tools ({state.server_tool_count} on server)"
            else:
                count = f"{state.tool_count} tools"
        kind = "stdio" if cfg.command else (cfg.transport or "http")
        target = cfg.command or cfg.url
        lines.append(f"{name:16} {status:12} {count:28} {kind:5} {target}{detail}")
    return "\n".join(lines)


def _add(agent: Agent, parts: list[str], ui: AgentUI) -> str:
    if not parts:
        raise ConfigError("usage: /mcp add <name> [preset|url|command...]")
    name, positionals, flags = _parse_add(parts)
    if not NAME_RE.match(name):
        raise ConfigError(f"server name {name!r} must match {NAME_RE.pattern}")
    preset = ""
    if positionals and positionals[0] in PRESET_NAMES and positionals[0] != name:
        preset = positionals.pop(0)
    elif name in PRESET_NAMES:
        preset = name
    if preset:
        config = build_preset(preset, name, positionals, flags)
    elif len(positionals) == 1 and positionals[0].startswith(("http://", "https://")):
        body: dict[str, Any] = {"url": positionals[0]}
        if flags.get("headers"):
            body["headers"] = dict(flags["headers"])
        if flags.get("transport"):
            body["transport"] = flags["transport"]
        if flags.get("oauth"):
            body["oauth"] = True
        config = parse_server(name, body)
    elif positionals:
        body = {"command": positionals[0], "args": positionals[1:]}
        env: dict[str, str] = {}
        if flags.get("client_id"):
            env["GOOGLE_OAUTH_CLIENT_ID"] = str(flags["client_id"])
        if flags.get("client_secret"):
            env["GOOGLE_OAUTH_CLIENT_SECRET"] = str(flags["client_secret"])
        if flags.get("token"):
            env["GITHUB_PERSONAL_ACCESS_TOKEN"] = str(flags["token"])
        if env:
            body["env"] = env
        if flags.get("oauth"):
            body["oauth"] = True
        config = parse_server(name, body)
    else:
        raise ConfigError(USAGE.strip())
    if flags.get("headers") and config.remote:
        config.headers.update(flags["headers"])
    path = upsert_server(config)
    lines = [f"added {name} ({'http' if config.remote else 'stdio'}) → {path}"]
    if preset == "google":
        lines.extend(_finish_google(agent, ui, config))
        return "\n".join(line for line in lines if line)
    note = preset_note(preset, config) if preset else ""
    connected = _after_write(agent, name)
    if note:
        lines.append(note)
    if connected:
        lines.append(connected)
    return "\n".join(lines)


def _remove(agent: Agent, parts: list[str]) -> str:
    if len(parts) != 1:
        raise ConfigError("usage: /mcp remove <name>")
    name = parts[0]
    servers = load_servers() if config_path().is_file() else {}
    cfg = servers.get(name)
    if not remove_server(name):
        return f"no MCP server named {name}"
    if cfg is not None and is_google_workspace(cfg):
        _clear_google(cfg)
    if not mcp_disabled():
        hub_for(agent).disconnect(name)
    return f"removed {name}"


def _login(agent: Agent, parts: list[str], ui: AgentUI) -> str:
    if len(parts) != 1:
        raise ConfigError("usage: /mcp login <name>")
    name = parts[0]
    servers = load_servers()
    cfg = servers.get(name)
    if cfg is None:
        return f"no MCP server named {name}"
    if is_google_workspace(cfg):
        if mcp_disabled():
            return _GOOGLE_DISABLED
        return _google_login(agent, ui, cfg)
    if not cfg.remote:
        return (
            f"{name} is a stdio server. Put credentials in its env block in "
            f"{config_path()}, then /mcp reload. /mcp login is for remote HTTP servers."
        )
    if mcp_disabled():
        return "MCP connections are off (HFAGENT_MCP_DISABLE)."
    return hub_for(agent).login(name)


def _logout(agent: Agent, parts: list[str]) -> str:
    if len(parts) != 1:
        raise ConfigError("usage: /mcp logout <name>")
    name = parts[0]
    servers = load_servers()
    cfg = servers.get(name)
    if cfg is None:
        return f"no MCP server named {name}"
    if is_google_workspace(cfg):
        _clear_google(cfg)
        cfg.env.pop("USER_GOOGLE_EMAIL", None)
        upsert_server(cfg)
        if not mcp_disabled():
            hub_for(agent).disconnect(name)
        return f"signed out of {name}. Google tokens removed. /mcp login {name} to sign in again."
    path = token_dir() / f"{name}.json"
    if path.is_file():
        path.unlink()
        return f"signed out of {name}"
    return f"{name} has no saved MCP tokens"


def _finish_google(agent: Agent, ui: AgentUI, config: ServerConfig) -> list[str]:
    from .google_oauth import PLACEHOLDER_MESSAGE, ensure_google_env

    client = ensure_google_env(config)
    upsert_server(config)
    if client.placeholder:
        return [PLACEHOLDER_MESSAGE]
    if mcp_disabled():
        return [
            "Saved google. Browser sign-in runs when MCP connections are enabled "
            "(HFAGENT_MCP_DISABLE is set)."
        ]
    message = _run_google_sign_in(agent, ui, config)
    connected = _after_write(agent, config.name)
    lines = [message]
    if connected:
        lines.append(connected)
    return lines


def _google_login(agent: Agent, ui: AgentUI, cfg: ServerConfig) -> str:
    from .google_oauth import PLACEHOLDER_MESSAGE, ensure_google_env

    client = ensure_google_env(cfg)
    upsert_server(cfg)
    if client.placeholder:
        return PLACEHOLDER_MESSAGE
    message = _run_google_sign_in(agent, ui, cfg)
    connected = _after_write(agent, cfg.name)
    if connected:
        return f"{message}\n{connected}"
    return message


def _run_google_sign_in(agent: Agent, ui: AgentUI, cfg: ServerConfig) -> str:
    from ..agent import system_prompt
    from .google_oauth import GoogleSignInError, client_for_config, remember_google_email, sign_in_google

    client = client_for_config(cfg)

    def on_status(text: str) -> None:
        try:
            ui.on_status(text)
        except Exception:
            return

    def on_notice(text: str) -> None:
        try:
            ui.on_system(text)
        except Exception:
            return

    try:
        result = sign_in_google(
            client,
            on_status=on_status,
            on_notice=on_notice,
            login_hint=cfg.env.get("USER_GOOGLE_EMAIL", ""),
        )
    except TimeoutError:
        on_status("Google sign-in timed out.")
        return "Google sign-in timed out. Run /mcp login google to try again."
    except GoogleSignInError as exc:
        on_status(str(exc))
        return str(exc)
    remember_google_email(cfg, result.email)
    if agent.messages and agent.messages[0].get("role") == "system":
        try:
            agent.messages[0]["content"] = system_prompt(agent.config.base_url)
        except Exception:
            pass
    message = f"Signed in to Google as {result.email}."
    if not result.refreshable:
        message += (
            " Google did not return a refresh token; run /mcp login google "
            "if a tool asks to sign in again."
        )
    return message


def _clear_google(cfg: ServerConfig) -> None:
    from .google_oauth import clear_google_credentials, forget_saved_google_email

    clear_google_credentials(cfg.env.get("USER_GOOGLE_EMAIL", ""))
    forget_saved_google_email()


def _tools(agent: Agent, parts: list[str]) -> str:
    if len(parts) > 1:
        raise ConfigError("usage: /mcp tools [name]")
    wanted = parts[0] if parts else ""
    names = agent.registry.names()
    rows: list[str] = []
    for tool_name in names:
        tool = agent.registry.get(tool_name)
        if tool is None or not hasattr(tool, "_server"):
            continue
        server = str(getattr(tool, "_server", ""))
        if wanted and server != wanted:
            continue
        remote = str(getattr(tool, "_remote", ""))
        mark = "approval" if tool.needs_approval else "read-only"
        rows.append(f"{tool_name}  {mark}  ({remote})")
    if not rows:
        if wanted:
            return f"no tools for {wanted}. /mcp reload if it is still connecting."
        return "no MCP tools connected."
    return "\n".join(rows)


def _reload(agent: Agent) -> str:
    if mcp_disabled():
        return "MCP connections are off (HFAGENT_MCP_DISABLE)."
    hub = hub_for(agent)
    try:
        hub.reload_sync(timeout=45)
    except Exception as exc:
        return f"reload failed: {exc}"
    return _listing(agent)


def _set_enabled(agent: Agent, action: str, parts: list[str]) -> str:
    if len(parts) != 1:
        raise ConfigError(f"usage: /mcp {action} <name>")
    name = parts[0]
    servers = load_servers()
    cfg = servers.get(name)
    if cfg is None:
        return f"no MCP server named {name}"
    cfg.enabled = action == "enable"
    upsert_server(cfg)
    if mcp_disabled():
        return f"{name} {'enabled' if cfg.enabled else 'disabled'} (connections are off)"
    return hub_for(agent).connect_now(name, timeout=45)


def _after_write(agent: Agent, name: str) -> str:
    if mcp_disabled():
        return ""
    try:
        return hub_for(agent).connect_now(name, timeout=25)
    except Exception as exc:
        return f"saved; connect failed: {exc}"


def _parse_add(parts: list[str]) -> tuple[str, list[str], dict[str, Any]]:
    name = parts[0]
    flags: dict[str, Any] = {"headers": {}}
    positionals: list[str] = []
    index = 1
    while index < len(parts):
        token = parts[index]
        if token == "--":
            positionals.extend(parts[index + 1 :])
            break
        if token == "--oauth":
            flags["oauth"] = True
            index += 1
            continue
        if token in {"--client-id", "--client-secret", "--token", "--transport"}:
            key = token[2:].replace("-", "_")
            if index + 1 >= len(parts):
                raise ConfigError(f"{token} needs a value")
            flags[key] = parts[index + 1]
            index += 2
            continue
        if token == "--header":
            if index + 1 >= len(parts):
                raise ConfigError("--header needs 'Name: value'")
            header_name, _, value = parts[index + 1].partition(":")
            header_name = header_name.strip()
            value = value.strip()
            if not header_name or not value:
                raise ConfigError("header must look like 'Name: value'")
            flags["headers"][header_name] = value
            index += 2
            continue
        if token.startswith("--"):
            raise ConfigError(f"unknown option {token}")
        positionals.append(token)
        index += 1
    return name, positionals, flags

