"""~/.hfagent/mcp.json — Claude Desktop / Cursor ``mcpServers`` shape.

Stdio servers use ``command``, ``args``, and ``env``. Remote servers use
``url`` and ``headers`` (Streamable HTTP, with SSE as a fallback). IHAgent
extensions on the same object: ``enabled``, ``autoApprove``, ``tools``,
``maxTools``, and ``oauth``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,32}$")

_KNOWN = {
    "command",
    "args",
    "env",
    "cwd",
    "url",
    "serverUrl",
    "headers",
    "transport",
    "type",
    "enabled",
    "disabled",
    "autoApprove",
    "tools",
    "maxTools",
    "oauth",
    "timeout",
    "callTimeout",
}

PRESET_NAMES = ("google", "filesystem", "github", "fetch")

DEFAULT_TIMEOUT = 20.0
DEFAULT_CALL_TIMEOUT = 60.0


def config_path() -> Path:
    override = os.environ.get("HFAGENT_MCP_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hfagent" / "mcp.json"


def token_dir() -> Path:
    override = os.environ.get("HFAGENT_MCP_TOKENS", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hfagent" / "mcp-tokens"


def log_dir() -> Path:
    override = os.environ.get("HFAGENT_MCP_LOG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hfagent" / "mcp-logs"


def mcp_disabled() -> bool:
    raw = os.environ.get("HFAGENT_MCP_DISABLE", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass
class ServerConfig:
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    transport: str = ""
    enabled: bool = True
    auto_approve: list[str] = field(default_factory=list)
    tools: list[str] | None = None
    max_tools: int | None = None
    oauth: bool = False
    oauth_scope: str = ""
    timeout: float = DEFAULT_TIMEOUT
    call_timeout: float = DEFAULT_CALL_TIMEOUT
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def remote(self) -> bool:
        return bool(self.url) and not self.command

    def to_json(self) -> dict[str, Any]:
        out = dict(self.extra)
        out.pop("disabled", None)
        out.pop("serverUrl", None)
        out.pop("type", None)
        if self.command:
            out["command"] = self.command
            out["args"] = list(self.args)
            if self.env:
                out["env"] = dict(self.env)
            else:
                out.pop("env", None)
            if self.cwd:
                out["cwd"] = self.cwd
            else:
                out.pop("cwd", None)
            out.pop("url", None)
            out.pop("headers", None)
        else:
            out.pop("command", None)
            out.pop("args", None)
            out.pop("env", None)
            out.pop("cwd", None)
            out["url"] = self.url
            if self.headers:
                out["headers"] = dict(self.headers)
            else:
                out.pop("headers", None)
        if self.transport:
            out["transport"] = self.transport
        else:
            out.pop("transport", None)
        if not self.enabled:
            out["enabled"] = False
        else:
            out.pop("enabled", None)
        if self.auto_approve:
            out["autoApprove"] = list(self.auto_approve)
        else:
            out.pop("autoApprove", None)
        if self.tools is not None:
            out["tools"] = list(self.tools)
        else:
            out.pop("tools", None)
        if self.max_tools is not None:
            out["maxTools"] = self.max_tools
        else:
            out.pop("maxTools", None)
        if self.oauth:
            out["oauth"] = {"scope": self.oauth_scope} if self.oauth_scope else True
        else:
            out.pop("oauth", None)
        if self.timeout != DEFAULT_TIMEOUT:
            out["timeout"] = self.timeout
        else:
            out.pop("timeout", None)
        if self.call_timeout != DEFAULT_CALL_TIMEOUT:
            out["callTimeout"] = self.call_timeout
        else:
            out.pop("callTimeout", None)
        return out


class ConfigError(ValueError):
    pass


def load_servers(path: Path | None = None) -> dict[str, ServerConfig]:
    path = config_path() if path is None else path
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a JSON object")
    block = data.get("mcpServers", data.get("servers", {}))
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise ConfigError(f"{path} mcpServers must be an object")
    servers: dict[str, ServerConfig] = {}
    for name, body in block.items():
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ConfigError(
                f"server name {name!r} must match {NAME_RE.pattern}"
            )
        if not isinstance(body, dict):
            raise ConfigError(f"server {name} must be an object")
        servers[name] = parse_server(name, body)
    return servers


def parse_server(name: str, body: dict[str, Any]) -> ServerConfig:
    extra = {key: value for key, value in body.items() if key not in _KNOWN}
    command = _string(body.get("command"))
    url = _string(body.get("url") or body.get("serverUrl"))
    if command and url:
        raise ConfigError(f"server {name} has both command and url")
    if not command and not url:
        raise ConfigError(f"server {name} needs a command or a url")
    transport = _string(body.get("transport") or body.get("type")).lower()
    if transport in {"streamable-http", "streamable_http", "http"}:
        transport = "http"
    elif transport in {"sse"}:
        transport = "sse"
    elif transport in {"stdio", ""}:
        transport = "stdio" if command and transport == "stdio" else transport
    elif transport:
        raise ConfigError(f"server {name} has unknown transport {transport!r}")
    enabled = True
    if "enabled" in body:
        enabled = _bool(body.get("enabled"), default=True)
    if _bool(body.get("disabled"), default=False):
        enabled = False
    oauth_flag, oauth_scope = _oauth(body.get("oauth"))
    tools = body.get("tools")
    tool_list: list[str] | None
    if tools is None:
        tool_list = None
    elif isinstance(tools, list) and all(isinstance(item, str) for item in tools):
        tool_list = list(tools)
    else:
        raise ConfigError(f"server {name} tools must be a list of names")
    max_tools = body.get("maxTools")
    max_value: int | None
    if max_tools is None:
        max_value = None
    elif isinstance(max_tools, int) and not isinstance(max_tools, bool) and max_tools >= 0:
        max_value = max_tools
    else:
        raise ConfigError(f"server {name} maxTools must be a non-negative integer")
    return ServerConfig(
        name=name,
        command=command,
        args=_str_list(body.get("args"), f"server {name} args"),
        env=_str_dict(body.get("env"), f"server {name} env"),
        cwd=_string(body.get("cwd")),
        url=url,
        headers=_str_dict(body.get("headers"), f"server {name} headers"),
        transport=transport,
        enabled=enabled,
        auto_approve=_str_list(body.get("autoApprove"), f"server {name} autoApprove"),
        tools=tool_list,
        max_tools=max_value,
        oauth=oauth_flag,
        oauth_scope=oauth_scope,
        timeout=_timeout(body.get("timeout"), DEFAULT_TIMEOUT, f"server {name} timeout"),
        call_timeout=_timeout(
            body.get("callTimeout"), DEFAULT_CALL_TIMEOUT, f"server {name} callTimeout"
        ),
        extra=extra,
    )


def save_servers(servers: dict[str, ServerConfig], path: Path | None = None) -> Path:
    path = config_path() if path is None else path
    body = {"mcpServers": {name: servers[name].to_json() for name in servers}}
    text = json.dumps(body, indent=2) + "\n"
    _atomic_write(path, text, 0o600)
    return path


def upsert_server(config: ServerConfig, path: Path | None = None) -> Path:
    path = config_path() if path is None else path
    servers = load_servers(path) if path.is_file() else {}
    if not NAME_RE.match(config.name):
        raise ConfigError(f"server name {config.name!r} must match {NAME_RE.pattern}")
    servers[config.name] = config
    return save_servers(servers, path)


def remove_server(name: str, path: Path | None = None) -> bool:
    path = config_path() if path is None else path
    servers = load_servers(path) if path.is_file() else {}
    if name not in servers:
        return False
    del servers[name]
    save_servers(servers, path)
    return True


def user_setting(name: str) -> str:
    """Process environment, then ~/.hfagent/.env, then ~/.hfagent/hf.env."""
    current = os.environ.get(name, "").strip()
    if current:
        return current
    try:
        from ..config import _parse_env_file
    except Exception:
        return ""
    home = Path.home() / ".hfagent"
    for filename in (".env", "hf.env"):
        found = _parse_env_file(home / filename)
        value = found.get(name, "").strip()
        if value:
            return value
    return ""


def build_preset(preset: str, name: str, positionals: list[str], flags: dict[str, Any]) -> ServerConfig:
    if preset == "google":
        body = _preset_google(flags)
    elif preset == "filesystem":
        body = _preset_filesystem(positionals)
    elif preset == "github":
        body = _preset_github(flags)
    elif preset == "fetch":
        body = {"command": "uvx", "args": ["mcp-server-fetch"]}
    else:
        raise ConfigError(f"unknown preset {preset!r}")
    if flags.get("oauth"):
        body["oauth"] = True
    return parse_server(name, body)


def preset_note(preset: str, config: ServerConfig) -> str:
    if preset == "google":
        missing = []
        if "GOOGLE_OAUTH_CLIENT_ID" not in config.env:
            missing.append("GOOGLE_OAUTH_CLIENT_ID")
        if "GOOGLE_OAUTH_CLIENT_SECRET" not in config.env:
            missing.append("GOOGLE_OAUTH_CLIENT_SECRET")
        note = (
            "Sign-in opens in the browser the first time a Gmail, Calendar, "
            "or Drive tool runs."
        )
        if missing:
            note += " Missing " + " and ".join(missing) + ". Pass --client-id and --client-secret, or export them."
        return note
    if preset == "github" and "GITHUB_PERSONAL_ACCESS_TOKEN" not in config.env:
        return "Set GITHUB_PERSONAL_ACCESS_TOKEN (--token or the environment) before GitHub tools will answer."
    if preset == "filesystem":
        folder = config.args[-1] if config.args else ""
        return f"Filesystem access is limited to {folder}."
    if preset == "fetch":
        return "Fetches public URLs. Writes are still approved when a tool is not marked read-only."
    return ""


def _preset_google(flags: dict[str, Any]) -> dict[str, Any]:
    env: dict[str, str] = {"OAUTHLIB_INSECURE_TRANSPORT": "1"}
    client_id = str(flags.get("client_id") or user_setting("GOOGLE_OAUTH_CLIENT_ID"))
    secret = str(flags.get("client_secret") or user_setting("GOOGLE_OAUTH_CLIENT_SECRET"))
    email = user_setting("USER_GOOGLE_EMAIL") or user_setting("GOOGLE_USER_EMAIL")
    if client_id:
        env["GOOGLE_OAUTH_CLIENT_ID"] = client_id
    if secret:
        env["GOOGLE_OAUTH_CLIENT_SECRET"] = secret
    if email:
        env["USER_GOOGLE_EMAIL"] = email
    return {
        "command": "uvx",
        "args": [
            "workspace-mcp",
            "--tools",
            "gmail",
            "drive",
            "calendar",
            "--tool-tier",
            "core",
        ],
        "env": env,
    }


def _preset_filesystem(positionals: list[str]) -> dict[str, Any]:
    raw = positionals[0] if positionals else os.getcwd()
    folder = str(Path(raw).expanduser().resolve())
    command, args = _npx(["@modelcontextprotocol/server-filesystem", folder])
    return {"command": command, "args": args}


def _preset_github(flags: dict[str, Any]) -> dict[str, Any]:
    token = str(
        flags.get("token")
        or user_setting("GITHUB_PERSONAL_ACCESS_TOKEN")
        or user_setting("GITHUB_TOKEN")
    )
    command, args = _npx(["@modelcontextprotocol/server-github"])
    body: dict[str, Any] = {"command": command, "args": args}
    if token:
        body["env"] = {"GITHUB_PERSONAL_ACCESS_TOKEN": token}
    return body


def _npx(package_args: list[str]) -> tuple[str, list[str]]:
    if os.name == "nt":
        return "cmd", ["/c", "npx", "-y", *package_args]
    return "npx", ["-y", *package_args]


def _atomic_write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    os.chmod(path, mode)


def _string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    raise ConfigError(f"expected a string, got {type(value).__name__}")


def _str_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{label} must be a list of strings")
    return list(value)


def _str_dict(value: Any, label: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be an object of strings")
    out: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ConfigError(f"{label} must be an object of strings")
        out[key] = item
    return out


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _oauth(value: Any) -> tuple[bool, str]:
    if value is None or value is False:
        return False, ""
    if value is True:
        return True, ""
    if isinstance(value, dict):
        scope = value.get("scope") or value.get("scopes") or ""
        if isinstance(scope, list):
            scope = " ".join(str(item) for item in scope)
        if not isinstance(scope, str):
            raise ConfigError("oauth.scope must be a string")
        return True, scope.strip()
    raise ConfigError("oauth must be true or an object")


def _timeout(value: Any, default: float, label: str) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{label} must be a number of seconds")
    if value <= 0:
        raise ConfigError(f"{label} must be positive")
    return float(value)
