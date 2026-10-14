"""Reasoning channels are thought, not an empty answer."""

import json

from hfagent.reasoning import ThinkSplitter
from tests.test_agent import Chunk, Delta, ToolCallDelta, make_agent


def test_think_tags_split_across_chunks():
    splitter = ThinkSplitter()
    answer = []
    thought = []
    for piece in ("<thi", "nk>hid", "den</thi", "nk>OK"):
        a, t = splitter.feed(piece)
        answer.append(a)
        thought.append(t)
    a, t = splitter.finish()
    answer.append(a)
    thought.append(t)
    assert "".join(answer) == "OK"
    assert "".join(thought) == "hidden"


def test_streaming_reasoning_fields_and_think_tags(tmp_path):
    turns = [
        [
            Chunk(Delta(reasoning="plan the reply")),
            Chunk(Delta(content="<think>more</think>visible answer")),
        ]
    ]
    agent, ui = make_agent(tmp_path, turns)
    final = agent.run("say something")
    assert final == "visible answer"
    heard = "".join(ui.thoughts)
    assert "plan the reply" in heard
    assert "more" in heard
    assert "<think>" not in final
    assert not any("empty" in err for err in ui.errors)
    stored = agent.messages[-1]["content"]
    assert stored == "visible answer"
    assert "<think>" not in stored


def test_reasoning_content_field_with_final_answer(tmp_path):
    turns = [[Chunk(Delta(content="the answer", reasoning_content="because"))]]
    agent, ui = make_agent(tmp_path, turns)
    final = agent.run("why")
    assert final == "the answer"
    assert "because" in "".join(ui.thoughts)
    assert not any("reasoning only" in status for status in ui.statuses)


def test_reasoning_plus_tool_call_is_not_empty(tmp_path):
    turns = [
        [
            Chunk(
                Delta(
                    reasoning="I should list the directory.",
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="call_1",
                            name="list_dir",
                            arguments=json.dumps({"path": str(tmp_path)}),
                        )
                    ],
                )
            )
        ],
        [Chunk(Delta(content="listed"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    final = agent.run("list this directory")
    assert final == "listed"
    assert ui.actions
    assert "I should list the directory." in "".join(ui.thoughts)
    assert not any("empty" in err for err in ui.errors)


def test_reasoning_only_nudges_then_accepts_an_answer(tmp_path):
    turns = [
        [Chunk(Delta(reasoning_content="just thinking"))],
        [Chunk(Delta(content="the answer"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    final = agent.run("answer me")
    assert final == "the answer"
    assert any("reasoning only" in status for status in ui.statuses)
    assert any(
        m.get("role") == "user" and "only reasoning" in (m.get("content") or "")
        for m in agent.messages
    )
    sent = json.dumps(agent.client.chat.completions.calls[-1]["messages"])
    assert "_reasoning_only" not in sent
    assert "only reasoning" in sent


def test_reasoning_only_eventually_rolls_back(tmp_path):
    turns = [[Chunk(Delta(reasoning="hmm"))] for _ in range(3)]
    agent, ui = make_agent(tmp_path, turns)
    final = agent.run("answer me")
    assert final == ""
    assert any("only reasoning" in err for err in ui.errors)


def test_nonstreaming_reasoning_and_answer(tmp_path):
    class Message:
        content = "<think>hidden</think>final"
        reasoning = "channel"
        reasoning_content = ""
        tool_calls = None

    class Choice:
        message = Message()

    class Completion:
        choices = [Choice()]
        usage = None

    class Client:
        def __init__(self):
            self.calls = []
            self.chat = self
            self.completions = self

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return Completion()

    agent, ui = make_agent(tmp_path, [])
    agent.client = Client()
    final = agent.run("go")
    assert final == "final"
    heard = "".join(ui.thoughts)
    assert "channel" in heard
    assert "hidden" in heard
    assert "<think>" not in final
