"""Offline tests for the agent loop using a fake streaming client."""

import json

from hfagent.agent import Agent, AgentUI, format_api_error, is_tool_choice_unsupported
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
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
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
        self.statuses = []
        self.allow = True

    def on_action(self, name, args):
        self.actions.append((name, args))

    def on_observation(self, name, result):
        self.observations.append((name, result))

    def on_assistant_delta(self, text):
        self.deltas.append(text)

    def on_error(self, message):
        self.errors.append(message)

    def on_status(self, text):
        self.statuses.append(text)

    def approve(self, name, args, preview=None):
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


VLLM_TOOL_400 = (
    "Error code: 400 - {'error': 'auto tool choice requires "
    "--enable-auto-tool-choice and --tool-call-parser to be set', "
    "'type': 'BadRequestError'}"
)


def test_format_api_error_unwraps_vllm_tool_choice():
    err = Exception(VLLM_TOOL_400)
    assert is_tool_choice_unsupported(err)
    text = format_api_error(err)
    assert "Retrying in text-tool mode" in text
    assert "BadRequestError" not in text
    assert "--enable-auto-tool-choice" not in text


def test_format_api_error_keeps_plain_message():
    assert format_api_error(RuntimeError("boom")) == "boom"


class FallbackClient:
    """First native-tools call fails like catalog vLLM; retry without tools."""

    def __init__(self, turns):
        class Chat:
            pass

        self.chat = Chat()
        self.chat.completions = self
        self.turns = list(turns)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if "tools" in kwargs:
            raise Exception(VLLM_TOOL_400)
        return iter(self.turns.pop(0))


def test_native_tools_400_retries_without_tools(tmp_path):
    agent, ui = make_agent(tmp_path, [])
    agent.client = FallbackClient([[Chunk(Delta(content="hello there"))]])
    final = agent.run("hello")
    assert final == "hello there"
    assert agent._native_tools is False
    assert len(agent.client.calls) == 2
    assert "tools" in agent.client.calls[0]
    assert "tools" not in agent.client.calls[1]


def test_repeated_failing_tool_gets_hint_and_nudge(tmp_path):
    """Same missing-path read_file must not burn all max_steps silently."""
    missing = str(tmp_path / "src" / "index.js")

    def fail_read(i):
        return [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id=f"call_{i}",
                            name="read_file",
                            arguments=json.dumps({"path": missing}),
                        )
                    ]
                )
            )
        ]

    # 2 identical failures → hint + nudge, then a plain final answer.
    turns = [fail_read(0), fail_read(1), [Chunk(Delta(content="Blocked on missing path."))]]
    agent, ui = make_agent(tmp_path, turns)
    agent.config.max_steps = 10
    agent.run("improve the webapp")

    assert any("HINT:" in obs for _, obs in ui.observations)
    assert any("same tool" in s for s in ui.statuses)
    assert any(
        m.get("role") == "user"
        and (
            "Stop repeating" in (m.get("content") or "")
            or "identical run_command" in (m.get("content") or "")
        )
        for m in agent.messages
    )


def test_repeated_failing_tool_rolls_back_after_five(tmp_path):
    missing = str(tmp_path / "gone.js")

    def fail_read(i):
        return [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id=f"call_{i}",
                            name="read_file",
                            arguments=json.dumps({"path": missing}),
                        )
                    ]
                )
            )
        ]

    agent, ui = make_agent(tmp_path, [fail_read(i) for i in range(8)])
    agent.config.max_steps = 10
    agent.run("improve the webapp")
    assert any("same failing tool" in e for e in ui.errors)
