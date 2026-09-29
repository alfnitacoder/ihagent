"""Slash-command dispatcher used by both the REPL and the TUI."""

from hfagent.agent import Agent, AgentUI
from hfagent.commands import HELP, dispatch_slash
from hfagent.config import Config
from hfagent.tools import default_registry


class Recorder(AgentUI):
    def __init__(self):
        self.status = []
        self.system = []
        self.errors = []

    def on_status(self, text):
        self.status.append(text)

    def on_system(self, text):
        self.system.append(text)

    def on_error(self, message):
        self.errors.append(message)


def _agent():
    return Agent(
        Config(api_key="none", base_url="http://127.0.0.1:9/v1", model="test"),
        default_registry(),
        AgentUI(),
    )


def test_help_and_tools_and_exit():
    agent = _agent()
    ui = Recorder()
    assert dispatch_slash(agent, ui, "/exit") == "exit"
    assert dispatch_slash(agent, ui, "/quit") == "exit"
    assert dispatch_slash(agent, ui, "/help") == "handled"
    assert HELP in ui.system[-1]
    assert dispatch_slash(agent, ui, "/tools") == "handled"
    assert "read_file" in ui.status[-1]


def test_clear_and_unknown():
    agent = _agent()
    ui = Recorder()
    agent.messages.append({"role": "user", "content": "hi"})
    assert dispatch_slash(agent, ui, "/clear") == "clear"
    assert agent.messages[0]["role"] == "system"
    assert len([m for m in agent.messages if m["role"] == "user"]) == 0
    assert dispatch_slash(agent, ui, "/nope") == "handled"
    assert "unknown command" in ui.system[-1]


def test_model_api_saves_key_without_echoing(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "HF_BASE_URL=https://ollama.com/v1\n# OLLAMA_API_KEY=commented\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("hfagent.config.project_env_path", lambda: path)
    agent = _agent()
    agent.config.base_url = "https://ollama.com/v1"
    ui = Recorder()
    secret = "cloud-key-example-1234"
    assert dispatch_slash(agent, ui, f"/model_api={secret}") == "handled"
    blob = "\n".join(ui.status + ui.system + ui.errors)
    assert secret not in blob
    assert ui.status[-1] == "api key saved"
    assert agent.config.api_key == secret
    assert agent.client.api_key == secret
    text = path.read_text(encoding="utf-8")
    assert f"OLLAMA_API_KEY={secret}" in text
    assert "# OLLAMA_API_KEY=commented" in text


def test_model_api_rejects_empty():
    agent = _agent()
    ui = Recorder()
    dispatch_slash(agent, ui, "/model_api=")
    assert ui.status[-1] == "paste the key as /model_api=..."
    assert agent.config.api_key == "none"


def test_cloud_and_local_switch_from_the_prompt(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "HF_BASE_URL=https://ollama.com/v1\nHFAGENT_MODEL=gpt-oss:120b\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("hfagent.config.project_env_path", lambda: path)
    monkeypatch.setenv("OLLAMA_API_KEY", "cloud-key-example-1234")
    monkeypatch.delenv("HFAGENT_LOCAL_MODEL", raising=False)
    monkeypatch.delenv("HFAGENT_CLOUD_MODEL", raising=False)
    monkeypatch.setattr(
        "hfagent.commands.list_model_names",
        lambda base_url, api_key="": ["qwen3:8b"],
    )
    agent = _agent()
    agent.config.base_url = "https://ollama.com/v1"
    agent.config.model = "gpt-oss:120b"
    ui = Recorder()

    assert dispatch_slash(agent, ui, "/local") == "handled"
    assert "11434" in agent.config.base_url
    assert agent.config.model == "qwen3:8b"
    assert "11434" in str(agent.client.base_url)
    text = path.read_text(encoding="utf-8")
    assert "HF_BASE_URL=http://127.0.0.1:11434/v1" in text
    assert "HFAGENT_CLOUD_MODEL=gpt-oss:120b" in text
    assert "HFAGENT_MODEL=qwen3:8b" in text
    assert "cloud-key-example-1234" not in "\n".join(ui.status + ui.system + ui.errors)

    assert dispatch_slash(agent, ui, "/cloud") == "handled"
    assert agent.config.base_url == "https://ollama.com/v1"
    assert agent.config.model == "gpt-oss:120b"
    assert agent.config.api_key == "cloud-key-example-1234"
    assert "ollama.com" in str(agent.client.base_url)
    text = path.read_text(encoding="utf-8")
    assert "HF_BASE_URL=https://ollama.com/v1" in text
    assert "HFAGENT_LOCAL_MODEL=qwen3:8b" in text


def test_approval_toggle():
    agent = _agent()
    ui = Recorder()
    dispatch_slash(agent, ui, "/approval auto")
    assert agent.config.approval == "auto"
    dispatch_slash(agent, ui, "/approval")
    assert "auto" in ui.status[-1]
