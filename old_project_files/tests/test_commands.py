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


def test_approval_toggle():
    agent = _agent()
    ui = Recorder()
    dispatch_slash(agent, ui, "/approval auto")
    assert agent.config.approval == "auto"
    dispatch_slash(agent, ui, "/approval")
    assert "auto" in ui.status[-1]
