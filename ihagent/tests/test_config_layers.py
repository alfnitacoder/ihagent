"""Env precedence, generic API key, and the config report."""

from hfagent.cli import main
from hfagent.config import (
    ANTHROPIC_BASE_URL,
    Config,
    api_key_info,
    format_config_report,
    normalize_base_url,
)


def _paths(monkeypatch, tmp_path):
    hf = tmp_path / "hf.env"
    user = tmp_path / "user.env"
    project = tmp_path / "project.env"
    hf.write_text("", encoding="utf-8")
    user.write_text("", encoding="utf-8")
    project.write_text("", encoding="utf-8")
    monkeypatch.setattr("hfagent.config.hf_env_path", lambda: hf)
    monkeypatch.setattr("hfagent.config.user_env_path", lambda: user)
    monkeypatch.setattr("hfagent.config.project_env_path", lambda: project)
    return hf, user, project


def test_sourced_hfenv_does_not_hide_the_user_file(monkeypatch, tmp_path):
    hf, user, _project = _paths(monkeypatch, tmp_path)
    hf.write_text("export HFAGENT_MODEL=old-from-hfenv\n", encoding="utf-8")
    user.write_text("HFAGENT_MODEL=new-from-user\n", encoding="utf-8")
    monkeypatch.setenv("HFAGENT_MODEL", "old-from-hfenv")
    cfg = Config.load()
    assert cfg.model == "new-from-user"
    assert cfg.sources["model"] == "~/.hfagent/.env"


def test_project_env_beats_user_env(monkeypatch, tmp_path):
    _hf, user, project = _paths(monkeypatch, tmp_path)
    user.write_text("HFAGENT_MODEL=from-user\n", encoding="utf-8")
    project.write_text("HFAGENT_MODEL=from-project\n", encoding="utf-8")
    monkeypatch.delenv("HFAGENT_MODEL", raising=False)
    cfg = Config.load()
    assert cfg.model == "from-project"
    assert cfg.sources["model"] == "project .env"


def test_explicit_process_env_beats_project_file(monkeypatch, tmp_path):
    hf, _user, project = _paths(monkeypatch, tmp_path)
    hf.write_text("export HFAGENT_MODEL=old-from-hfenv\n", encoding="utf-8")
    project.write_text("HFAGENT_MODEL=from-project\n", encoding="utf-8")
    monkeypatch.setenv("HFAGENT_MODEL", "from-shell")
    cfg = Config.load()
    assert cfg.model == "from-shell"
    assert cfg.sources["model"] == "process environment"


def test_command_line_beats_process_env(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    monkeypatch.setenv("HFAGENT_MODEL", "from-shell")
    cfg = Config.load(model="from-cli")
    assert cfg.model == "from-cli"
    assert cfg.sources["model"] == "command line"


def test_generic_api_key_beats_stale_hf_token(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    monkeypatch.setenv("HFAGENT_API_KEY", "right-key-12345678")
    monkeypatch.setenv("HF_TOKEN", "stale-token-12345678")
    cfg = Config.load(base_url="https://abc.trycloudflare.com/v1")
    assert cfg.api_key == "right-key-12345678"
    assert cfg.api_key_var == "HFAGENT_API_KEY"
    report = format_config_report(cfg)
    assert "right-key-12345678" not in report
    assert "stale-token-12345678" not in report
    assert "righ…5678" in report
    assert "HFAGENT_API_KEY" in report
    assert "command line" in report  # base URL came from the flag


def test_provider_keys_still_work_when_generic_is_unset(monkeypatch):
    monkeypatch.delenv("HFAGENT_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anth-key-123456")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setenv("HF_TOKEN", "hf-token-123456")
    claude = Config.load(base_url=ANTHROPIC_BASE_URL)
    assert claude.api_key == "anth-key-123456"
    assert claude.api_key_var == "ANTHROPIC_API_KEY"
    cloud = Config.load(base_url="https://ollama.com/v1")
    assert cloud.api_key == "hf-token-123456"
    assert cloud.api_key_var == "HF_TOKEN"
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-key-123456")
    cloud = Config.load(base_url="https://ollama.com/v1")
    assert cloud.api_key == "ollama-key-123456"
    monkeypatch.setenv("HFAGENT_API_KEY", "generic-key-123456")
    assert api_key_info("https://ollama.com/v1")[0] == "generic-key-123456"
    assert api_key_info(ANTHROPIC_BASE_URL)[0] == "generic-key-123456"


def test_custom_host_without_a_key_suggests_generic_var(monkeypatch):
    monkeypatch.delenv("HFAGENT_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HF_API_KEY", raising=False)
    key, var = api_key_info("https://tunnel.example/v1")
    assert key == ""
    assert var == "HFAGENT_API_KEY"


def test_context_tokens_are_configurable(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    monkeypatch.setenv("HFAGENT_CONTEXT_TOKENS", "32768")
    cfg = Config.load()
    assert cfg.context_tokens == 32768
    assert "32768" in format_config_report(cfg)


def test_show_config_flag_masks_secrets(monkeypatch, capsys):
    monkeypatch.setenv("HFAGENT_API_KEY", "sk-supersecretvalue")
    monkeypatch.setenv("HF_BASE_URL", "https://tunnel.example/v1")
    main(["--show-config"])
    out = capsys.readouterr().out
    assert "sk-supersecretvalue" not in out
    assert "https://tunnel.example/v1" in out
    assert "process environment" in out
    assert "HFAGENT_API_KEY" in out
    assert "command line > process environment > project .env" in out


def test_probe_names_auth_and_dns(monkeypatch):
    import json
    from urllib.error import HTTPError, URLError

    from hfagent.config import probe_base_url

    class Body:
        def __init__(self, payload: bytes):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def ok(req, timeout=8):
        return Body(json.dumps({"data": [{"id": "qwen"}]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", ok)
    reached, detail, names, kind = probe_base_url(
        "https://abc.trycloudflare.com/v1", "secret"
    )
    assert reached and names == ["qwen"] and kind == "ok"
    assert "abc.trycloudflare.com/v1/models" in detail

    def deny(req, timeout=8):
        raise HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", deny)
    reached, detail, names, kind = probe_base_url(
        "https://abc.trycloudflare.com/v1", ""
    )
    assert not reached and kind == "auth" and names == []
    assert "401/403 auth" in detail
    assert "HFAGENT_API_KEY" in detail

    def dns(req, timeout=8):
        raise URLError(OSError("Temporary failure in name resolution"))

    monkeypatch.setattr("urllib.request.urlopen", dns)
    reached, detail, _names, kind = probe_base_url("https://abc.trycloudflare.com/v1", "")
    assert not reached and kind == "dns"
    assert "DNS failure" in detail


def test_normalize_bare_tunnel_url():
    assert (
        normalize_base_url("https://abc.trycloudflare.com")
        == "https://abc.trycloudflare.com/v1"
    )
    assert (
        normalize_base_url("https://abc.trycloudflare.com/v1")
        == "https://abc.trycloudflare.com/v1"
    )
    assert (
        normalize_base_url("https://abc.trycloudflare.com/v1/")
        == "https://abc.trycloudflare.com/v1"
    )
    assert normalize_base_url("abc.trycloudflare.com") == "https://abc.trycloudflare.com/v1"
    assert normalize_base_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434/v1"
