"""Unreachable primary hosts switch to the optional fallback."""

from hfagent.agent import Agent
from hfagent.commands import dispatch_slash
from tests.test_agent import Chunk, Delta, Recorder, make_agent


class _Connect(Exception):
    def __init__(self):
        super().__init__("Connection error.")


class _Auth(Exception):
    def __init__(self):
        super().__init__("Error code: 401 - invalid api key")
        self.status_code = 401


class _Flaky:
    def __init__(self, turns, exc):
        self.turns = list(turns)
        self.exc = exc
        self.calls = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            exc = self.exc
            self.exc = None
            raise exc
        return iter(self.turns.pop(0))


def _keep_client(agent):
    def retarget(**kwargs):
        client = agent.client
        Agent.retarget(agent, **kwargs)
        agent.client = client

    agent.retarget = retarget


def test_connection_failure_switches_to_fallback(tmp_path):
    agent, ui = make_agent(tmp_path, [])
    client = _Flaky([[Chunk(Delta(content="ok from fallback"))]], _Connect())
    agent.client = client
    _keep_client(agent)
    agent.config.base_url = "https://down.trycloudflare.com/v1"
    agent.config.model = "primary-model"
    agent.config.fallback_base_url = "http://127.0.0.1:11434/v1"
    agent.config.fallback_model = "fallback-model"
    final = agent.run("hello")
    assert final == "ok from fallback"
    assert agent.on_fallback
    assert agent.config.base_url == "http://127.0.0.1:11434/v1"
    assert agent.config.model == "fallback-model"
    blob = "\n".join(ui.statuses)
    assert "down.trycloudflare.com" in blob
    assert "fallback" in blob.lower()
    assert "/primary" in blob
    assert agent.primary_target[0] == "https://down.trycloudflare.com/v1"
    assert len(client.calls) == 2


def test_auth_failure_does_not_switch(tmp_path):
    agent, ui = make_agent(tmp_path, [])
    agent.client = _Flaky([], _Auth())
    _keep_client(agent)
    original = "https://down.trycloudflare.com/v1"
    agent.config.base_url = original
    agent.config.fallback_base_url = "http://127.0.0.1:11434/v1"
    agent.config.fallback_model = "fallback-model"
    try:
        agent.run("hello")
    except _Auth:
        pass
    else:
        raise AssertionError("auth errors must surface")
    assert agent.config.base_url == original
    assert not agent.on_fallback
    assert not any("fallback" in status.lower() for status in ui.statuses)


def test_primary_switches_back_and_refuses_when_still_down(monkeypatch, tmp_path):
    agent, ui = make_agent(tmp_path, [])
    client = _Flaky([[Chunk(Delta(content="ok from fallback"))]], _Connect())
    agent.client = client
    _keep_client(agent)
    agent.config.base_url = "https://down.trycloudflare.com/v1"
    agent.config.fallback_base_url = "https://ollama.com/v1"
    agent.config.fallback_model = "fallback-model"
    assert agent.run("hello") == "ok from fallback"

    monkeypatch.setattr(
        "hfagent.commands.probe_base_url",
        lambda base, key="": (False, "Cannot reach primary: DNS failure", [], "dns"),
    )
    ui2 = Recorder()
    dispatch_slash(agent, ui2, "/primary")
    assert agent.on_fallback
    assert "still unreachable" in ui2.errors[-1]

    monkeypatch.setattr(
        "hfagent.commands.probe_base_url",
        lambda base, key="": (True, "reachable", ["primary-model"], "ok"),
    )
    dispatch_slash(agent, ui2, "/primary")
    assert not agent.on_fallback
    assert agent.config.base_url == "https://down.trycloudflare.com/v1"
    assert any("switched back" in status for status in ui2.statuses)