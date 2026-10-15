"""MCP client: config, namespacing, stdio calls, errors, approval, OAuth files."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from pathlib import Path
from urllib.request import urlopen

import pytest

from hfagent.agent import Agent, AgentUI
from hfagent.commands import dispatch_slash
from hfagent.config import Config
from hfagent.mcp.config import build_preset, load_servers, parse_server, save_servers
from hfagent.mcp.oauth import FileTokenStorage, LoopbackCallback
from hfagent.mcp.session import (
    approval_required,
    exposed_name,
    is_auth_error,
    suggests_sse,
)
from hfagent.mcp.session import McpHub
from hfagent.tools import ToolRegistry

FAKE = Path(__file__).parent / "mcp_fake_server.py"


class Recorder(AgentUI):
    def __init__(self) -> None:
        self.status: list[str] = []
        self.system: list[str] = []
        self.errors: list[str] = []

    def on_status(self, text: str) -> None:
        self.status.append(text)

    def on_system(self, text: str) -> None:
        self.system.append(text)

    def on_error(self, message: str) -> None:
        self.errors.append(message)


def _isolate(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "mcp.json"
    monkeypatch.setenv("HFAGENT_MCP_CONFIG", str(path))
    monkeypatch.setenv("HFAGENT_MCP_TOKENS", str(tmp_path / "tokens"))
    monkeypatch.setenv("HFAGENT_MCP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("HFAGENT_MCP_DISABLE", raising=False)
    monkeypatch.setattr("hfagent.mcp.config.user_setting", lambda name: "")
    return path


def _fake_body(env: dict | None = None, **extra) -> dict:
    body = {
        "command": sys.executable,
        "args": [str(FAKE)],
        "env": {key: str(value) for key, value in (env or {}).items()},
        "timeout": 15,
        "callTimeout": 6,
    }
    body.update(extra)
    return body


def _agent(registry: ToolRegistry | None = None) -> Agent:
    return Agent(
        Config(api_key="none", base_url="http://127.0.0.1:9/v1", model="test"),
        registry or ToolRegistry(),
        Recorder(),
    )


def _start(monkeypatch, tmp_path: Path, servers: dict) -> tuple[McpHub, ToolRegistry]:
    path = _isolate(monkeypatch, tmp_path)
    save_servers(
        {name: parse_server(name, body) for name, body in servers.items()},
        path,
    )
    registry = ToolRegistry()
    hub = McpHub(registry, lambda: None, config_file=path, tokens=tmp_path / "tokens")
    hub.start()
    hub.wait_settled(20)
    return hub, registry


def test_exposed_names_are_namespaced_and_short():
    assert exposed_name("google", "search_gmail") == "google__search_gmail"
    assert exposed_name("my.server", "read file") == "my_server__read_file"
    long = exposed_name("calendar", "x" * 80)
    assert len(long) <= 64
    assert long.startswith("calendar__")
    assert approval_required("echo", read_only=True, destructive=False, auto_approve=set()) is False
    assert approval_required("send_note", read_only=True, destructive=False, auto_approve=set()) is True
    assert approval_required("lookup", read_only=False, destructive=False, auto_approve=set()) is True
    assert approval_required(
        "send_note", read_only=False, destructive=False, auto_approve={"send_note"}
    ) is False
    assert approval_required("delete_row", read_only=False, destructive=True, auto_approve={"*"}) is False
    assert suggests_sse(RuntimeError("HTTP 406 not acceptable")) is True
    assert suggests_sse(RuntimeError("connection refused")) is False
    assert is_auth_error(RuntimeError("401 Unauthorized")) is True


def test_presets_do_not_hardcode_an_account(monkeypatch):
    monkeypatch.setattr("hfagent.mcp.config.user_setting", lambda name: "")
    google = build_preset("google", "google", [], {})
    assert google.command == "uvx"
    assert "gmail" in google.args and "drive" in google.args and "calendar" in google.args
    assert "--tool-tier" in google.args and "core" in google.args
    blob = json.dumps(google.to_json())
    assert "alffy.george@gmail.com" not in blob
    assert "GOOGLE_OAUTH_CLIENT_ID" not in google.env
    files = build_preset("filesystem", "filesystem", ["/tmp/notes"], {})
    assert files.args[-1] == "/tmp/notes"
    assert "@modelcontextprotocol/server-filesystem" in files.args
    github = build_preset("github", "github", [], {"token": "ghp_example"})
    assert github.env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_example"
    fetch = build_preset("fetch", "fetch", [], {})
    assert fetch.args == ["mcp-server-fetch"]


def test_config_roundtrip_and_limits(tmp_path: Path):
    path = tmp_path / "mcp.json"
    save_servers(
        {
            "docs": parse_server(
                "docs",
                {
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer secret"},
                    "oauth": {"scope": "user"},
                    "tools": ["search"],
                    "maxTools": 4,
                    "autoApprove": ["search"],
                },
            )
        },
        path,
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = load_servers(path)
    assert loaded["docs"].url == "https://example.com/mcp"
    assert loaded["docs"].oauth is True
    assert loaded["docs"].oauth_scope == "user"
    assert loaded["docs"].tools == ["search"]
    assert loaded["docs"].max_tools == 4
    assert loaded["docs"].headers["Authorization"] == "Bearer secret"
    text = path.read_text(encoding="utf-8")
    assert "mcpServers" in text


def test_stdio_list_call_namespace_and_errors(monkeypatch, tmp_path: Path):
    hub, registry = _start(
        monkeypatch,
        tmp_path,
        {
            "alpha": _fake_body({"FAKE_MCP_ID": "alpha"}),
            "beta": _fake_body({"FAKE_MCP_ID": "beta"}, tools=["echo", "send_note"], maxTools=5),
        },
    )
    try:
        states = {state.name: state for state in hub.states()}
        assert states["alpha"].status == "ready"
        assert states["alpha"].tool_count == 5
        assert "alpha__echo" in registry.names()
        assert "beta__echo" in registry.names()
        assert "beta__send_note" in registry.names()
        assert "beta__boom" not in registry.names()
        echo = registry.get("alpha__echo")
        assert echo is not None and echo.needs_approval is False
        assert registry.get("alpha__send_note").needs_approval is True
        spec = echo.spec()
        assert spec["type"] == "function"
        assert spec["function"]["parameters"]["type"] == "object"
        assert echo.run(text="hi") == "echo:alpha:hi"
        assert registry.get("beta__echo").run(text="yo") == "echo:beta:yo"
        boom = registry.get("alpha__boom").run()
        assert boom.startswith("Error:")
        assert "nope" in boom
        portrait = registry.get("alpha__portrait").run()
        assert "image image/png" in portrait
        assert "aGk=" not in portrait
    finally:
        hub.stop()


def test_stdio_crash_reconnects_once(monkeypatch, tmp_path: Path):
    crash = tmp_path / "crashed"
    hub, registry = _start(
        monkeypatch,
        tmp_path,
        {"box": _fake_body({"FAKE_MCP_CRASH": "once", "FAKE_MCP_CRASH_FILE": str(crash), "FAKE_MCP_ID": "box"})},
    )
    try:
        result = registry.get("box__echo").run(text="back")
        assert "echo:box:back" in result
        assert crash.is_file()
    finally:
        hub.stop()


def test_stdio_crash_always_is_an_error_string(monkeypatch, tmp_path: Path):
    hub, registry = _start(
        monkeypatch,
        tmp_path,
        {"box": _fake_body({"FAKE_MCP_CRASH": "always"})},
    )
    try:
        result = registry.get("box__echo").run(text="x")
        assert isinstance(result, str)
        assert result.startswith("Error:")
    finally:
        hub.stop()


def test_bad_command_does_not_raise(monkeypatch, tmp_path: Path):
    hub, registry = _start(
        monkeypatch,
        tmp_path,
        {"gone": {"command": "ihagent-mcp-missing-bin", "args": ["--nope"], "timeout": 5}},
    )
    try:
        state = hub.states()[0]
        assert state.status == "error"
        assert state.detail
        assert registry.names() == []
    finally:
        hub.stop()


def test_execute_marks_iheye_working(monkeypatch, tmp_path: Path):
    flags: list[dict] = []
    monkeypatch.setattr("hfagent.iheye.publish_activity", lambda **kw: flags.append(kw))
    hub, registry = _start(monkeypatch, tmp_path, {"demo": _fake_body({"FAKE_MCP_ID": "demo"})})
    try:
        agent = Agent(
            Config(api_key="none", base_url="http://127.0.0.1:9/v1", model="test", approval="auto"),
            registry,
            AgentUI(),
        )
        flags.clear()
        result = agent._execute(
            {"function": {"name": "demo__echo", "arguments": json.dumps({"text": "hi"})}}
        )
        assert result == "echo:demo:hi"
        assert {"working": True} in flags
        flags.clear()
        direct = registry.get("demo__echo")
        assert direct is not None
        assert direct.run(text="again") == "echo:demo:again"
        assert flags == [{"working": True}]
        declined = Agent(
            Config(api_key="none", base_url="http://127.0.0.1:9/v1", model="test", approval="default"),
            registry,
            AgentUI(),
        )._execute(
            {"function": {"name": "demo__send_note", "arguments": json.dumps({"body": "hello"})}}
        )
        assert "declined" in declined.lower()
    finally:
        hub.stop()


def test_auto_approve_skips_send(monkeypatch, tmp_path: Path):
    hub, registry = _start(
        monkeypatch,
        tmp_path,
        {"mail": _fake_body(autoApprove=["send_note"], tools=["send_note"])},
    )
    try:
        tool = registry.get("mail__send_note")
        assert tool is not None
        assert tool.needs_approval is False
        assert tool.run(body="ping") == "sent:ping"
    finally:
        hub.stop()


def test_token_file_is_private(tmp_path: Path):
    from mcp.shared.auth import OAuthToken

    path = tmp_path / "mcp-tokens" / "docs.json"
    store = FileTokenStorage(path)

    async def write() -> None:
        await store.set_tokens(
            OAuthToken(access_token="access-token-value", refresh_token="refresh-token-value", token_type="Bearer")
        )

    asyncio.run(write())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["tokens"]["refresh_token"] == "refresh-token-value"

    async def read() -> str:
        tokens = await store.get_tokens()
        return tokens.access_token

    assert asyncio.run(read()) == "access-token-value"


def test_loopback_callback_captures_code():
    callback = LoopbackCallback(timeout=5)
    callback.start()
    try:
        url = f"{callback.redirect_uri}?code=abc&state=xyz&iss=https%3A%2F%2Fauth.example"

        def fetch() -> None:
            with urlopen(url, timeout=5) as response:
                assert b"signed in" in response.read().lower()

        import threading

        thread = threading.Thread(target=fetch)
        thread.start()

        async def wait():
            return await callback.wait_result()

        result = asyncio.run(wait())
        thread.join(5)
        assert result.code == "abc"
        assert result.state == "xyz"
        assert result.iss == "https://auth.example"
    finally:
        callback.stop()


def test_slash_add_remove_enable_without_connecting(monkeypatch, tmp_path: Path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("HFAGENT_MCP_DISABLE", "1")
    agent = _agent()
    ui = agent.ui
    assert dispatch_slash(agent, ui, "/mcp") == "handled"
    assert "no servers" in ui.system[-1]
    assert dispatch_slash(agent, ui, "/mcp add google --client-id id.apps.googleusercontent.com --client-secret secret") == "handled"
    saved = json.loads(Path(os.environ["HFAGENT_MCP_CONFIG"]).read_text(encoding="utf-8"))
    google = saved["mcpServers"]["google"]
    assert google["command"] == "uvx"
    assert google["env"]["GOOGLE_OAUTH_CLIENT_ID"] == "id.apps.googleusercontent.com"
    assert "alffy.george@gmail.com" not in json.dumps(saved)
    assert dispatch_slash(agent, ui, "/mcp add files https://example.com/mcp --header 'Authorization: Bearer tok'") == "handled"
    saved = json.loads(Path(os.environ["HFAGENT_MCP_CONFIG"]).read_text(encoding="utf-8"))
    assert saved["mcpServers"]["files"]["url"] == "https://example.com/mcp"
    assert saved["mcpServers"]["files"]["headers"]["Authorization"] == "Bearer tok"
    assert dispatch_slash(agent, ui, "/mcp disable google") == "handled"
    assert "disabled" in ui.system[-1]
    assert dispatch_slash(agent, ui, "/mcp login google") == "handled"
    assert "browser" in ui.system[-1].lower()
    assert dispatch_slash(agent, ui, "/mcp remove files") == "handled"
    saved = json.loads(Path(os.environ["HFAGENT_MCP_CONFIG"]).read_text(encoding="utf-8"))
    assert "files" not in saved["mcpServers"]
    assert dispatch_slash(agent, ui, "/mcp tools") == "handled"
    assert "no MCP tools" in ui.system[-1]


def test_oauth_provider_uses_pkce_metadata():
    pytest.importorskip("mcp")
    from mcp.client.auth import OAuthClientProvider

    from hfagent.mcp.oauth import build_provider

    callback = LoopbackCallback(timeout=1)
    callback.start()
    try:
        store = FileTokenStorage(Path(os.devnull))
        provider = build_provider(
            "https://example.com/mcp",
            store,
            callback,
            scope="user",
            interactive=False,
        )
        assert isinstance(provider, OAuthClientProvider)
        meta = provider.context.client_metadata
        assert str(meta.redirect_uris[0]).startswith("http://127.0.0.1:")
        assert meta.token_endpoint_auth_method == "none"
        assert "authorization_code" in meta.grant_types
        assert meta.scope == "user"
    finally:
        callback.stop()
