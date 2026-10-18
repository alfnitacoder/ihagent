"""Google one-click OAuth: client precedence, placeholder, email, login."""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import urlopen

import pytest

from hfagent.agent import Agent, system_prompt
from hfagent.commands import dispatch_slash
from hfagent.config import Config
from hfagent.mcp.config import load_servers
from hfagent.mcp.google_oauth import (
    PLACEHOLDER_MESSAGE,
    GoogleOAuthClient,
    GoogleSignInError,
    authorization_url,
    email_from_id_token,
    google_client_file,
    load_bundled_google_client,
    main as bake_main,
    resolve_google_client,
    sign_in_google,
)
from hfagent.mcp.session import McpHub
from hfagent.tools import ToolRegistry


class Recorder:
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


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HFAGENT_MCP_CONFIG", str(tmp_path / "mcp.json"))
    monkeypatch.setenv("HFAGENT_MCP_TOKENS", str(tmp_path / "tokens"))
    monkeypatch.setenv("HFAGENT_MCP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("HFAGENT_MCP_DISABLE", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("USER_GOOGLE_EMAIL", raising=False)
    monkeypatch.setattr("hfagent.mcp.config.user_setting", lambda name: os.environ.get(name, ""))
    monkeypatch.setattr("hfagent.config.user_env_path", lambda: tmp_path / "user.env")
    monkeypatch.setattr("hfagent.mcp.google_oauth.BUILD_GOOGLE_OAUTH_CLIENT_ID", "")
    monkeypatch.setattr("hfagent.mcp.google_oauth.BUILD_GOOGLE_OAUTH_CLIENT_SECRET", "")


def _agent() -> Agent:
    return Agent(
        Config(api_key="none", base_url="http://127.0.0.1:9/v1", model="test"),
        ToolRegistry(),
        Recorder(),
    )


def _jwt(payload: dict) -> str:
    head = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode("ascii")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode("ascii")
    return f"{head}.{body}.sig"


def _bundled(client_id: str, secret: str, *, placeholder: bool) -> GoogleOAuthClient:
    return GoogleOAuthClient(client_id, secret, "bundled", placeholder)


def test_shipped_client_file_is_marked():
    data = json.loads(google_client_file().read_text(encoding="utf-8"))
    assert "client_id" in data and "client_secret" in data and "placeholder" in data
    if data["placeholder"] is True:
        assert "PLACEHOLDER" in str(data["client_id"]).upper()
    else:
        assert str(data["client_id"]).endswith(".apps.googleusercontent.com")
        assert data["client_secret"]
        assert "PLACEHOLDER" not in str(data["client_id"]).upper()


def test_client_precedence(monkeypatch, tmp_path: Path):
    _isolate(monkeypatch, tmp_path)
    placeholder_file = tmp_path / "google_client.json"
    placeholder_file.write_text(
        json.dumps(
            {
                "client_id": "PLACEHOLDER.apps.googleusercontent.com",
                "client_secret": "PLACEHOLDER",
                "placeholder": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("hfagent.mcp.google_oauth.google_client_file", lambda: placeholder_file)

    bundled = resolve_google_client({})
    assert bundled.placeholder is True
    assert bundled.source == "bundled"

    monkeypatch.setattr("hfagent.mcp.google_oauth.BUILD_GOOGLE_OAUTH_CLIENT_ID", "built.apps.googleusercontent.com")
    monkeypatch.setattr("hfagent.mcp.google_oauth.BUILD_GOOGLE_OAUTH_CLIENT_SECRET", "built-secret")
    built = resolve_google_client({})
    assert built.source == "build"
    assert built.client_id == "built.apps.googleusercontent.com"
    assert built.placeholder is False

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-secret")
    from_env = resolve_google_client({})
    assert from_env.source == "env"
    assert from_env.client_id == "env.apps.googleusercontent.com"

    from_flags = resolve_google_client(
        {"client_id": "flag.apps.googleusercontent.com", "client_secret": "flag-secret"}
    )
    assert from_flags.source == "flags"
    assert from_flags.client_id == "flag.apps.googleusercontent.com"
    assert "flag-secret" not in PLACEHOLDER_MESSAGE

    with pytest.raises(Exception, match="both --client-id"):
        resolve_google_client({"client_id": "only-id"})
    with pytest.raises(Exception, match="both GOOGLE_OAUTH_CLIENT_ID"):
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET")
        resolve_google_client({})


def test_real_bundled_file_beats_placeholder_marker(monkeypatch, tmp_path: Path):
    _isolate(monkeypatch, tmp_path)
    path = tmp_path / "google_client.json"
    path.write_text(
        json.dumps(
            {
                "client_id": "real.apps.googleusercontent.com",
                "client_secret": "real-secret",
                "placeholder": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("hfagent.mcp.google_oauth.google_client_file", lambda: path)
    client = load_bundled_google_client()
    assert client.placeholder is False
    assert client.client_id == "real.apps.googleusercontent.com"


def test_placeholder_add_does_not_open_a_browser(monkeypatch, tmp_path: Path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("HFAGENT_MCP_DISABLE", "1")

    def fail_open(_url: str) -> bool:
        raise AssertionError("browser should not open for a placeholder client")

    monkeypatch.setattr("hfagent.mcp.google_oauth._open_browser", fail_open)
    agent = _agent()
    ui = agent.ui
    assert dispatch_slash(agent, ui, "/mcp add google") == "handled"
    text = ui.system[-1]
    assert text.splitlines()[-1] == PLACEHOLDER_MESSAGE
    assert "\n" not in PLACEHOLDER_MESSAGE
    assert "Missing GOOGLE_OAUTH_CLIENT_ID" not in text
    saved = json.loads(Path(os.environ["HFAGENT_MCP_CONFIG"]).read_text(encoding="utf-8"))
    env = saved["mcpServers"]["google"]["env"]
    assert "GOOGLE_OAUTH_CLIENT_ID" not in env
    assert "GOOGLE_OAUTH_CLIENT_SECRET" not in env
    assert env["WORKSPACE_MCP_CREDENTIALS_DIR"].endswith("google-workspace")
    assert dispatch_slash(agent, ui, "/mcp login google") == "handled"
    assert "browser" in ui.system[-1].lower()
    assert "placeholder" in ui.system[-1].lower() or "HFAGENT_MCP_DISABLE" in ui.system[-1]


def test_login_stores_email_and_tokens(monkeypatch, tmp_path: Path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr("hfagent.mcp.session.McpHub.connect_now", lambda self, name, timeout=30: f"{name} connected (2 tools)")
    agent = _agent()
    ui = agent.ui
    secret = "desktop-client-secret"
    access = "access-token-value"
    refresh = "refresh-token-value"
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        assert secret not in url
        assert access not in url
        assert refresh not in url
        qs = parse_qs(urlparse(url).query)
        redirect = qs["redirect_uri"][0]
        state = qs["state"][0]
        with urlopen(redirect + "?code=auth-code&state=" + quote(state), timeout=5) as response:
            assert b"signed in" in response.read().lower()
        return True

    def fake_exchange(**kwargs):
        assert kwargs["code"] == "auth-code"
        assert kwargs["client_secret"] == secret
        assert kwargs["code_verifier"]
        return {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": 3600,
            "id_token": _jwt({"email": "user@example.com", "email_verified": True}),
            "scope": "openid https://www.googleapis.com/auth/userinfo.email",
            "token_type": "Bearer",
        }

    monkeypatch.setattr("hfagent.mcp.google_oauth.exchange_code", fake_exchange)
    monkeypatch.setattr("hfagent.mcp.google_oauth._open_browser", fake_open)
    assert dispatch_slash(
        agent,
        ui,
        f"/mcp add google --client-id id.apps.googleusercontent.com --client-secret {secret}",
    ) == "handled"
    assert "Signed in to Google as user@example.com." in ui.system[-1]
    ui.system.clear()
    ui.status.clear()
    opened.clear()
    assert dispatch_slash(agent, ui, "/mcp login google") == "handled"
    shown = "\n".join(ui.status + ui.system)
    assert "opening the browser" in shown
    assert "waiting for approval" in shown
    assert "Signed in to Google as user@example.com." in ui.system[-1]
    assert "connected" in ui.system[-1]
    assert secret not in shown
    assert access not in shown
    assert refresh not in shown
    assert opened and opened[0].startswith("https://accounts.google.com/")

    saved = json.loads(Path(os.environ["HFAGENT_MCP_CONFIG"]).read_text(encoding="utf-8"))
    env = saved["mcpServers"]["google"]["env"]
    assert env["USER_GOOGLE_EMAIL"] == "user@example.com"
    assert env["GOOGLE_OAUTH_CLIENT_ID"] == "id.apps.googleusercontent.com"
    creds = list((tmp_path / "tokens" / "google-workspace").glob("*.json"))
    assert len(creds) == 1
    assert creds[0].name == "user@example.com.json"
    assert stat.S_IMODE(creds[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(creds[0].parent.stat().st_mode) == 0o700
    stored = json.loads(creds[0].read_text(encoding="utf-8"))
    assert stored["refresh_token"] == refresh
    assert stored["token"] == access
    assert stored["client_secret"] == secret

    user_env = (tmp_path / "user.env").read_text(encoding="utf-8")
    assert "USER_GOOGLE_EMAIL=user@example.com" in user_env
    assert secret not in user_env
    prompt = system_prompt("http://127.0.0.1:9/v1")
    assert "user@example.com" in prompt
    assert "Do not ask the user for an email address" in prompt
    assert secret not in prompt and access not in prompt
    assert "user@example.com" in agent.messages[0]["content"]

    hub = McpHub(agent.registry, lambda: None, config_file=Path(os.environ["HFAGENT_MCP_CONFIG"]), tokens=tmp_path / "tokens")

    class _Remote:
        name = "search_gmail_messages"
        description = "Search mail"
        input_schema = {
            "type": "object",
            "properties": {
                "user_google_email": {"type": "string", "description": "ask the user"},
                "query": {"type": "string", "description": "query"},
            },
            "required": ["user_google_email"],
        }
        annotations = None

    cfg = load_servers()["google"]
    hub._install(cfg, [_Remote()])
    tool = agent.registry.get("google__search_gmail_messages")
    assert tool is not None
    assert "user@example.com" in tool.description
    assert "Do not ask the user for an email address" in tool.description
    field = tool.parameters["properties"]["user_google_email"]
    assert field["default"] == "user@example.com"
    assert "Do not ask the user for an email address" in field["description"]
    assert tool.parameters["properties"]["query"]["description"] == "query"

    assert dispatch_slash(agent, ui, "/mcp logout google") == "handled"
    assert "tokens removed" in ui.system[-1]
    assert secret not in ui.system[-1]
    assert access not in ui.system[-1]
    assert not creds[0].exists()
    saved = json.loads(Path(os.environ["HFAGENT_MCP_CONFIG"]).read_text(encoding="utf-8"))
    assert "USER_GOOGLE_EMAIL" not in saved["mcpServers"]["google"]["env"]
    assert "USER_GOOGLE_EMAIL" not in (tmp_path / "user.env").read_text(encoding="utf-8")


def test_login_uses_userinfo_when_id_token_has_no_email(monkeypatch, tmp_path: Path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr("hfagent.mcp.session.McpHub.connect_now", lambda self, name, timeout=30: "")

    def fake_open(url: str) -> bool:
        qs = parse_qs(urlparse(url).query)
        redirect = qs["redirect_uri"][0]
        state = qs["state"][0]
        with urlopen(redirect + "?code=c&state=" + quote(state), timeout=5) as response:
            response.read()
        return True

    monkeypatch.setattr("hfagent.mcp.google_oauth._open_browser", fake_open)
    monkeypatch.setattr(
        "hfagent.mcp.google_oauth.exchange_code",
        lambda **_kwargs: {"access_token": "tok", "refresh_token": "ref", "expires_in": 10, "scope": "openid"},
    )
    monkeypatch.setattr("hfagent.mcp.google_oauth.fetch_userinfo", lambda _token: "other@example.com")
    agent = _agent()
    assert dispatch_slash(
        agent,
        agent.ui,
        "/mcp add google --client-id id.apps.googleusercontent.com --client-secret desktop-client-secret",
    ) == "handled"
    assert "Signed in to Google as other@example.com." in agent.ui.system[-1]
    assert "tok" not in "\n".join(agent.ui.status + agent.ui.system)


def test_sign_in_times_out(monkeypatch):
    client = GoogleOAuthClient("id.apps.googleusercontent.com", "desktop-client-secret", "flags", False)
    notes: list[str] = []
    with pytest.raises(TimeoutError, match="timed out"):
        sign_in_google(
            client,
            on_status=notes.append,
            timeout=0.3,
            open_browser=lambda _url: True,
        )
    assert any("opening the browser" in line for line in notes)
    assert any("waiting for approval" in line for line in notes)
    assert "desktop-client-secret" not in "\n".join(notes)


def test_sign_in_rejects_a_mismatched_state():
    client = GoogleOAuthClient("id.apps.googleusercontent.com", "desktop-client-secret", "flags", False)

    def bad_open(url: str) -> bool:
        redirect = parse_qs(urlparse(url).query)["redirect_uri"][0]
        with urlopen(redirect + "?code=abc&state=nope", timeout=5) as response:
            response.read()
        return True

    with pytest.raises(GoogleSignInError, match="did not match"):
        sign_in_google(client, on_status=lambda _text: None, open_browser=bad_open, timeout=5)


def test_cancelled_sign_in():
    client = GoogleOAuthClient("id.apps.googleusercontent.com", "desktop-client-secret", "flags", False)

    def deny(url: str) -> bool:
        qs = parse_qs(urlparse(url).query)
        redirect = qs["redirect_uri"][0]
        state = qs["state"][0]
        with urlopen(redirect + "?error=access_denied&state=" + quote(state), timeout=5) as response:
            response.read()
        return True

    with pytest.raises(GoogleSignInError, match="cancelled"):
        sign_in_google(client, on_status=lambda _text: None, open_browser=deny, timeout=5)


def test_remove_clears_google_tokens(monkeypatch, tmp_path: Path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("HFAGENT_MCP_DISABLE", "1")
    monkeypatch.setattr("hfagent.mcp.google_oauth._open_browser", lambda _url: True)
    agent = _agent()
    dispatch_slash(
        agent,
        agent.ui,
        "/mcp add google --client-id id.apps.googleusercontent.com --client-secret desktop-client-secret",
    )
    folder = tmp_path / "tokens" / "google-workspace"
    folder.mkdir(parents=True, exist_ok=True)
    cred = folder / "user@example.com.json"
    cred.write_text('{"token": "access-token-value"}\n', encoding="utf-8")
    os.chmod(cred, 0o600)
    dispatch_slash(agent, agent.ui, "/mcp remove google")
    assert not cred.exists()
    saved = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
    assert "google" not in saved["mcpServers"]
    assert "access-token-value" not in "\n".join(agent.ui.system)


def test_bake_writes_client_without_printing_the_secret(monkeypatch, tmp_path: Path, capsys):
    target = tmp_path / "google_client.json"
    monkeypatch.setattr("hfagent.mcp.google_oauth.google_client_file", lambda: target)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "baked.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "baked-secret-value")
    assert bake_main(["--bake"]) == 0
    out = capsys.readouterr().out
    assert "baked-secret-value" not in out
    assert "not printed" in out
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["placeholder"] is False
    assert data["client_id"] == "baked.apps.googleusercontent.com"
    assert data["client_secret"] == "baked-secret-value"


def test_authorization_url_has_no_secret():
    url = authorization_url(
        client_id="id.apps.googleusercontent.com",
        redirect_uri="http://127.0.0.1:9/",
        state="state",
        code_challenge="challenge",
    )
    assert "client_secret" not in url
    assert url.startswith("https://accounts.google.com/")


def test_email_from_id_token():
    assert email_from_id_token(_jwt({"email": "a@b.co"})) == "a@b.co"
    assert email_from_id_token("not-a-jwt") == ""
    assert email_from_id_token(_jwt({"email": "not-an-email"})) == ""


def test_prompt_mentions_google_account_without_a_saved_server(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HFAGENT_MCP_CONFIG", str(tmp_path / "missing.json"))
    text = system_prompt("http://127.0.0.1:9/v1")
    assert "Do not ask the user for an email address" in text
