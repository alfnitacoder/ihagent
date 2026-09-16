"""Offline tests for the agent loop using a fake streaming client."""

import json

from hfagent.agent import Agent, AgentUI
from hfagent.config import Config
from hfagent.tools import default_registry


# ----------------------------------------------------------- fake openai client

class Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class ToolCallDelta:
    def __init__(self, index, id=None, name=None, arguments=None):
        self.index = index
        self.id = id

        class Fn:
            pass

        self.function = Fn()
        self.function.name = name
        self.function.arguments = arguments


class Chunk:
    def __init__(self, delta):
        class Choice:
            pass

        choice = Choice()
        choice.delta = delta
        self.choices = [choice]


class FakeCompletions:
    def __init__(self, turns):
        self.turns = list(turns)

    def create(self, **kwargs):
        return iter(self.turns.pop(0))


class FakeClient:
    def __init__(self, turns):
        class Chat:
            pass

        self.chat = Chat()
        self.chat.completions = FakeCompletions(turns)


class Recorder(AgentUI):
    def __init__(self):
        self.actions = []
        self.observations = []
        self.approvals = []
        self.deltas = []
        self.errors = []
        self.allow = True

    def on_action(self, name, args):
        self.actions.append((name, args))

    def on_observation(self, name, result):
        self.observations.append((name, result))

    def on_assistant_delta(self, text):
        self.deltas.append(text)

    def on_error(self, message):
        self.errors.append(message)

    def approve(self, name, args):
        self.approvals.append((name, args))
        return self.allow


def make_agent(tmp_path, turns):
    config = Config(api_key="fake", base_url="http://127.0.0.1:9", model="fake-model")
    ui = Recorder()
    agent = Agent(config, default_registry(), ui)
    agent.client = FakeClient(turns)
    return agent, ui


# ------------------------------------------------------------------- the tests

def test_full_tool_loop(tmp_path):
    (tmp_path / "hello.txt").write_text("hi there")
    turns = [
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="call_1",
                            name="list_dir",
                            arguments=json.dumps({"path": str(tmp_path)}),
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content="It contains hello")), Chunk(Delta(content=".txt"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    final = agent.run("what is in this directory?")

    assert final == "It contains hello.txt"
    assert ui.actions == [("list_dir", {"path": str(tmp_path)})]
    assert ui.deltas == ["It contains hello", ".txt"]
    roles = [m["role"] for m in agent.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    tool_msg = agent.messages[3]
    assert tool_msg["tool_call_id"] == "call_1"
    assert "hello.txt" in tool_msg["content"]


def test_approval_gate_blocks_denied_tool(tmp_path):
    turns = [
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="call_1",
                            name="run_command",
                            arguments=json.dumps({"command": "touch x"}),
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content="done"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    ui.allow = False
    agent.run("please run it")

    assert ui.approvals == [("run_command", {"command": "touch x"})]
    declined = agent.messages[3]["content"]
    assert "declined" in declined
    assert not (tmp_path / "x").exists()


def test_max_steps_guard(tmp_path):
    # every turn asks for a tool call -> loop must stop at max_steps
    def tool_turn(i):
        return [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id=f"call_{i}",
                            name="list_dir",
                            arguments=json.dumps({"path": str(tmp_path)}),
                        )
                    ]
                )
            )
        ]

    agent, ui = make_agent(tmp_path, [tool_turn(i) for i in range(10)])
    agent.config.max_steps = 3
    agent.run("loop forever")
    assert any("max steps" in e for e in ui.errors)
    assert len(agent.messages) == 1 + 1 + 3 * 2  # system + user + 3 (assistant+tool)


def test_unknown_tool_returns_error_observation(tmp_path):
    turns = [
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(0, id="call_1", name="nope", arguments="{}")
                    ]
                )
            )
        ],
        [Chunk(Delta(content="understood"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    agent.run("do the thing")
    assert "unknown tool" in agent.messages[3]["content"]
