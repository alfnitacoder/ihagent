"""/reload re-reads env files into the live session."""

import os

from hfagent.agent import Agent, AgentUI
from hfagent.commands import HELP, dispatch_slash
from hfagent.config import Config, set_runtime_env
from hfagent.tools import default_registry

from tests.test_commands import Recorder


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
    # /url looks up the name imported into commands, not the config module.
    monkeypatch.setattr("hfagent.commands.project_env_path", lambda: project)
    return hf, user, project


def _clear_host_env(monkeypatch):
    for name in (
        "HF_BASE_URL",
        "HFAGENT_MODEL",
        "HFAGENT_API_KEY",
        "HF_TOKEN",
        "HF_API_KEY",
        "OLLAMA_API_KEY",
        "ANTHROPIC_API_KEY",
        "HFAGENT_APPROVAL",
        "HFAGENT_YOLO",
        "HFAGENT_MAX_STEPS",
        "HFAGENT_FALLBACK_BASE_URL",
        "HFAGENT_CLOUD_MODEL",
        "HFAGENT_LOCAL_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def _agent(config: Config) -> Agent:
    agent = Agent(config, default_registry(), AgentUI())
    agent.messages.append({"role": "user", "content": "keep me"})
    agent.messages.append({"role": "assistant", "content": "still here"})
    agent.session_name = "sess-keep"
    return agent


def _blob(ui: Recorder) -> str:
    return "\n".join(ui.status + ui.system + ui.errors)


def _kept(agent: Agent) -> None:
    assert agent.session_name == "sess-keep"
    assert [m.get("content") for m in agent.messages if m.get("role") != "system"] == [
        "keep me",
        "still here",
    ]
    assert agent.messages[0]["role"] == "system"


def test_reload_is_documented():
    assert "/reload" in HELP
    assert "shell export" in HELP
    assert "fallback host" in HELP


def test_reload_sees_a_file_edit_after_startup(monkeypatch, tmp_path):
    _hf, _user, project = _paths(monkeypatch, tmp_path)
    _clear_host_env(monkeypatch)
    project.write_text(
        "HF_BASE_URL=https://old.example/v1\n"
        "HFAGENT_MODEL=old-model\n"
        "HFAGENT_API_KEY=old-key-12345678\n",
        encoding="utf-8",
    )
    agent = _agent(Config.load())
    assert os.environ["HF_BASE_URL"] == "https://old.example/v1"
    secret = "edited-key-12345678"
    project.write_text(
        "HF_BASE_URL=https://edited.example/v1\n"
        "HFAGENT_MODEL=edited-model\n"
        f"HFAGENT_API_KEY={secret}\n"
        "HFAGENT_MAX_STEPS=11\n",
        encoding="utf-8",
    )
    before = agent.client
    ui = Recorder()
    assert dispatch_slash(agent, ui, "/reload") == "handled"
    assert agent.config.base_url == "https://edited.example/v1"
    assert agent.config.model == "edited-model"
    assert agent.config.api_key == secret
    assert agent.config.api_key_var == "HFAGENT_API_KEY"
    assert agent.config.max_steps == 11
    assert agent.config.sources["base_url"] == "project .env"
    assert agent.config.sources["model"] == "project .env"
    assert agent.client is not before
    assert agent.client.api_key == secret
    assert "edited.example/v1" in str(agent.client.base_url)
    _kept(agent)
    blob = _blob(ui)
    assert secret not in blob
    assert "old-key-12345678" not in blob
    assert "https://edited.example/v1" in blob
    assert "edited-model" in blob
    assert "project .env" in blob
    assert "HFAGENT_API_KEY" in blob
    assert ui.status[-1] == "reloaded · custom · edited-model"


def test_runtime_write_does_not_hide_a_later_file_edit(monkeypatch, tmp_path):
    _hf, _user, project = _paths(monkeypatch, tmp_path)
    _clear_host_env(monkeypatch)
    project.write_text(
        "HF_BASE_URL=https://old.example/v1\nHFAGENT_MODEL=old-model\n",
        encoding="utf-8",
    )
    Config.load()
    set_runtime_env("HF_BASE_URL", "https://runtime.example/v1")
    set_runtime_env("HFAGENT_MODEL", "runtime-model")
    project.write_text(
        "HF_BASE_URL=https://edited.example/v1\nHFAGENT_MODEL=edited-model\n",
        encoding="utf-8",
    )
    cfg = Config.load()
    assert cfg.base_url == "https://edited.example/v1"
    assert cfg.model == "edited-model"
    assert cfg.sources["base_url"] == "project .env"


def test_reload_after_url_uses_the_edited_file(monkeypatch, tmp_path):
    _hf, user, project = _paths(monkeypatch, tmp_path)
    _clear_host_env(monkeypatch)
    project.write_text(
        "HF_BASE_URL=https://old.example/v1\n"
        "HFAGENT_MODEL=old-model\n"
        "HFAGENT_API_KEY=old-key-12345678\n",
        encoding="utf-8",
    )
    agent = _agent(Config.load())
    monkeypatch.setattr(
        "hfagent.commands.probe_base_url",
        lambda base, key="": (True, "reachable", ["old-model"], "ok"),
    )
    ui = Recorder()
    assert dispatch_slash(agent, ui, "/url https://switched.example") == "handled"
    assert os.environ["HF_BASE_URL"] == "https://switched.example/v1"
    assert "HF_BASE_URL=https://switched.example/v1" in project.read_text(encoding="utf-8")
    assert "HF_BASE_URL=https://switched.example/v1" in user.read_text(encoding="utf-8")
    secret = "edited-key-12345678"
    project.write_text(
        "HF_BASE_URL=https://edited.example/v1\n"
        "HFAGENT_MODEL=edited-model\n"
        f"HFAGENT_API_KEY={secret}\n",
        encoding="utf-8",
    )
    before = agent.client
    assert dispatch_slash(agent, ui, "/reload") == "handled"
    assert agent.config.base_url == "https://edited.example/v1"
    assert agent.config.model == "edited-model"
    assert agent.config.api_key == secret
    assert agent.client is not before
    assert agent.client.api_key == secret
    assert "edited.example/v1" in str(agent.client.base_url)
    assert agent.config.sources["base_url"] == "project .env"
    _kept(agent)
    blob = _blob(ui)
    assert secret not in blob
    assert "old-key-12345678" not in blob
    assert "https://edited.example/v1" in blob


def test_reload_after_cloud_sees_the_edited_file(monkeypatch, tmp_path):
    _hf, _user, project = _paths(monkeypatch, tmp_path)
    _clear_host_env(monkeypatch)
    project.write_text(
        "HF_BASE_URL=http://127.0.0.1:11434/v1\nHFAGENT_MODEL=qwen3:8b\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OLLAMA_API_KEY", "cloud-key-12345678")
    monkeypatch.setenv("HFAGENT_CLOUD_MODEL", "gpt-oss:120b")
    agent = _agent(Config.load())
    ui = Recorder()
    assert dispatch_slash(agent, ui, "/cloud") == "handled"
    assert agent.config.base_url == "https://ollama.com/v1"
    assert os.environ["HF_BASE_URL"] == "https://ollama.com/v1"
    secret = "edited-key-12345678"
    project.write_text(
        "HF_BASE_URL=https://edited.example/v1\n"
        "HFAGENT_MODEL=edited-model\n"
        f"HFAGENT_API_KEY={secret}\n",
        encoding="utf-8",
    )
    assert dispatch_slash(agent, ui, "/reload") == "handled"
    assert agent.config.base_url == "https://edited.example/v1"
    assert agent.config.model == "edited-model"
    assert agent.config.api_key == secret
    assert agent.client.api_key == secret
    _kept(agent)
    blob = _blob(ui)
    assert secret not in blob
    assert "cloud-key-12345678" not in blob


def test_reload_sees_an_edited_key_after_model_api(monkeypatch, tmp_path):
    _hf, _user, project = _paths(monkeypatch, tmp_path)
    _clear_host_env(monkeypatch)
    project.write_text(
        "HF_BASE_URL=https://tunnel.example/v1\n"
        "HFAGENT_MODEL=tunnel-model\n"
        "HFAGENT_API_KEY=old-key-12345678\n",
        encoding="utf-8",
    )
    agent = _agent(Config.load())
    ui = Recorder()
    pasted = "pasted-key-12345678"
    assert dispatch_slash(agent, ui, f"/model_api={pasted}") == "handled"
    assert os.environ["HFAGENT_API_KEY"] == pasted
    secret = "edited-key-12345678"
    text = project.read_text(encoding="utf-8").replace(pasted, secret)
    project.write_text(text, encoding="utf-8")
    before = agent.client
    assert dispatch_slash(agent, ui, "/reload") == "handled"
    assert agent.config.api_key == secret
    assert agent.client is not before
    assert agent.client.api_key == secret
    blob = _blob(ui)
    assert pasted not in blob
    assert secret not in blob
    assert "old-key-12345678" not in blob
    _kept(agent)


def test_shell_export_still_beats_a_file_edit(monkeypatch, tmp_path):
    _hf, _user, project = _paths(monkeypatch, tmp_path)
    _clear_host_env(monkeypatch)
    project.write_text(
        "HF_BASE_URL=https://file.example/v1\n"
        "HFAGENT_MODEL=from-file\n"
        "HFAGENT_API_KEY=file-key-12345678\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HF_BASE_URL", "https://shell.example/v1")
    monkeypatch.setenv("HFAGENT_MODEL", "from-shell")
    monkeypatch.setenv("HFAGENT_API_KEY", "shell-key-12345678")
    agent = _agent(Config.load())
    assert agent.config.sources["base_url"] == "process environment"
    project.write_text(
        "HF_BASE_URL=https://edited.example/v1\n"
        "HFAGENT_MODEL=edited-model\n"
        "HFAGENT_API_KEY=edited-key-12345678\n",
        encoding="utf-8",
    )
    ui = Recorder()
    dispatch_slash(agent, ui, "/reload")
    assert agent.config.base_url == "https://shell.example/v1"
    assert agent.config.model == "from-shell"
    assert agent.config.api_key == "shell-key-12345678"
    assert agent.config.sources["model"] == "process environment"
    blob = _blob(ui)
    assert "process environment" in blob
    assert "edited-key-12345678" not in blob
    assert "file-key-12345678" not in blob
    assert "shell-key-12345678" not in blob
    _kept(agent)


def test_shell_export_beats_a_key_saved_by_model_api(monkeypatch, tmp_path):
    _hf, _user, project = _paths(monkeypatch, tmp_path)
    _clear_host_env(monkeypatch)
    project.write_text(
        "HF_BASE_URL=https://tunnel.example/v1\nHFAGENT_MODEL=tunnel-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HFAGENT_API_KEY", "shell-key-12345678")
    agent = _agent(Config.load())
    ui = Recorder()
    pasted = "pasted-key-12345678"
    dispatch_slash(agent, ui, f"/model_api={pasted}")
    assert agent.config.api_key == pasted
    text = project.read_text(encoding="utf-8").replace(pasted, "edited-key-12345678")
    project.write_text(text, encoding="utf-8")
    dispatch_slash(agent, ui, "/reload")
    assert agent.config.api_key == "shell-key-12345678"
    assert agent.client.api_key == "shell-key-12345678"
    blob = _blob(ui)
    assert pasted not in blob
    assert "edited-key-12345678" not in blob
    assert "shell-key-12345678" not in blob


def test_sourced_hfenv_edit_shows_up_on_reload(monkeypatch, tmp_path):
    hf, _user, _project = _paths(monkeypatch, tmp_path)
    _clear_host_env(monkeypatch)
    hf.write_text(
        "export HF_BASE_URL=https://old-hf.example/v1\n"
        "export HFAGENT_MODEL=old-hf-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HF_BASE_URL", "https://old-hf.example/v1")
    monkeypatch.setenv("HFAGENT_MODEL", "old-hf-model")
    agent = _agent(Config.load())
    assert agent.config.sources["base_url"] == "~/.hfagent/hf.env"
    hf.write_text(
        "export HF_BASE_URL=https://new-hf.example/v1\n"
        "export HFAGENT_MODEL=new-hf-model\n",
        encoding="utf-8",
    )
    ui = Recorder()
    dispatch_slash(agent, ui, "/reload")
    assert agent.config.base_url == "https://new-hf.example/v1"
    assert agent.config.model == "new-hf-model"
    assert agent.config.sources["base_url"] == "~/.hfagent/hf.env"
    assert agent.config.sources["model"] == "~/.hfagent/hf.env"
    assert "https://new-hf.example/v1" in _blob(ui)
    assert "~/.hfagent/hf.env" in _blob(ui)
    _kept(agent)


def test_home_dotenv_beats_user_env_on_reload(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".hfagent").mkdir(parents=True)
    (home / ".env").write_text(
        "HF_BASE_URL=https://from-home.example/v1\nHFAGENT_MODEL=from-home\n",
        encoding="utf-8",
    )
    (home / ".hfagent" / ".env").write_text(
        "HF_BASE_URL=https://from-user.example/v1\nHFAGENT_MODEL=from-user\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("hfagent.config.Path.home", lambda: home)
    monkeypatch.chdir(home)
    _clear_host_env(monkeypatch)
    agent = _agent(Config.load())
    assert agent.config.model == "from-home"
    assert agent.config.sources["model"] == "project .env"
    (home / ".env").write_text(
        "HF_BASE_URL=https://from-home-edited.example/v1\n"
        "HFAGENT_MODEL=from-home-edited\n",
        encoding="utf-8",
    )
    (home / ".hfagent" / ".env").write_text(
        "HF_BASE_URL=https://from-user-edited.example/v1\n"
        "HFAGENT_MODEL=from-user-edited\n",
        encoding="utf-8",
    )
    ui = Recorder()
    dispatch_slash(agent, ui, "/reload")
    assert agent.config.base_url == "https://from-home-edited.example/v1"
    assert agent.config.model == "from-home-edited"
    assert agent.config.sources["base_url"] == "project .env"
    assert agent.config.sources["model"] == "project .env"
    assert "from-user-edited" not in _blob(ui)
    _kept(agent)


def test_reload_warns_when_the_new_host_has_no_key(monkeypatch, tmp_path):
    _hf, _user, project = _paths(monkeypatch, tmp_path)
    _clear_host_env(monkeypatch)
    project.write_text(
        "HF_BASE_URL=https://ollama.com/v1\nHFAGENT_MODEL=gpt-oss:120b\n",
        encoding="utf-8",
    )
    agent = _agent(Config.load())
    ui = Recorder()
    dispatch_slash(agent, ui, "/reload")
    assert agent.config.base_url == "https://ollama.com/v1"
    assert ui.status[-1] == "host: cloud — paste your key as /model_api=..."
    assert "api key (unset)" in _blob(ui)
    _kept(agent)

    project.write_text(
        "HF_BASE_URL=https://api.anthropic.com/v1\nHFAGENT_MODEL=claude-sonnet-5-5\n",
        encoding="utf-8",
    )
    dispatch_slash(agent, ui, "/reload")
    assert ui.status[-1] == "host: claude — paste your Anthropic key as /model_api=..."
    assert agent.config.model == "claude-sonnet-5-5"

    project.write_text(
        "HF_BASE_URL=http://127.0.0.1:11434/v1\nHFAGENT_MODEL=qwen3:8b\n",
        encoding="utf-8",
    )
    dispatch_slash(agent, ui, "/reload")
    assert agent.config.base_url == "http://127.0.0.1:11434/v1"
    assert ui.status[-1] == "reloaded · local · qwen3:8b"
    assert "paste your key" not in ui.status[-1]
    _kept(agent)


def test_reload_replaces_session_only_changes(monkeypatch, tmp_path):
    _hf, _user, project = _paths(monkeypatch, tmp_path)
    _clear_host_env(monkeypatch)
    project.write_text(
        "HF_BASE_URL=https://primary.example/v1\nHFAGENT_MODEL=primary-model\n",
        encoding="utf-8",
    )
    agent = _agent(Config.load())
    ui = Recorder()
    dispatch_slash(agent, ui, "/approval auto")
    assert agent.config.approval == "auto"
    agent.on_fallback = True
    agent.primary_target = (
        "https://primary.example/v1",
        "",
        "primary-model",
        "",
    )
    agent.config.base_url = "https://fallback.example/v1"
    agent.config.model = "fallback-model"
    dispatch_slash(agent, ui, "/reload")
    assert agent.config.approval == "default"
    assert agent.config.base_url == "https://primary.example/v1"
    assert agent.config.model == "primary-model"
    assert agent.on_fallback is False
    assert agent.primary_target is None
    _kept(agent)

    project.write_text(
        "HF_BASE_URL=https://primary.example/v1\n"
        "HFAGENT_MODEL=primary-model\n"
        "HFAGENT_APPROVAL=auto\n",
        encoding="utf-8",
    )
    dispatch_slash(agent, ui, "/approval default")
    dispatch_slash(agent, ui, "/reload")
    assert agent.config.approval == "auto"
    assert agent.config.sources["approval"] == "project .env"


def test_command_line_model_still_wins(monkeypatch, tmp_path):
    _hf, _user, project = _paths(monkeypatch, tmp_path)
    _clear_host_env(monkeypatch)
    project.write_text(
        "HF_BASE_URL=https://file.example/v1\nHFAGENT_MODEL=from-file\n",
        encoding="utf-8",
    )
    agent = _agent(Config.load(model="from-cli"))
    agent.launch_overrides = {"model": "from-cli"}
    assert agent.config.model == "from-cli"
    project.write_text(
        "HF_BASE_URL=https://edited.example/v1\nHFAGENT_MODEL=edited-model\n",
        encoding="utf-8",
    )
    ui = Recorder()
    dispatch_slash(agent, ui, "/reload")
    assert agent.config.model == "from-cli"
    assert agent.config.sources["model"] == "command line"
    assert agent.config.base_url == "https://edited.example/v1"
    assert agent.config.sources["base_url"] == "project .env"
    assert "command line" in _blob(ui)


def test_make_agent_remembers_launch_flags(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    _clear_host_env(monkeypatch)
    from hfagent.cli import build_parser, make_agent

    args = build_parser().parse_args(
        ["--model", "from-cli", "--base-url", "http://127.0.0.1:9/v1", "--max-steps", "3"]
    )
    agent, _ui = make_agent(args, require_key=False)
    assert agent.launch_overrides["model"] == "from-cli"
    assert agent.launch_overrides["base_url"] == "http://127.0.0.1:9/v1"
    assert agent.launch_overrides["max_steps"] == 3
    assert agent.config.model == "from-cli"
    assert agent.config.max_steps == 3
