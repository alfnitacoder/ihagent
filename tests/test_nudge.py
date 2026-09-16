"""Tests for the propose-vs-act nudge in the agent loop."""

from tests.test_agent import Chunk, Delta, ToolCallDelta, make_agent


def test_nudge_fires_on_proposed_command(tmp_path):
    (tmp_path / "real.txt").write_text("real content\n")
    turns = [
        # turn 1: proposes a command instead of calling a tool
        [Chunk(Delta(content="Sure! Run this:\n```sh\nls ~\n```\nWould you like me to run it?"))],
        # turn 2 (after nudge): actually calls the tool
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="call_1",
                            name="list_dir",
                            arguments='{"path": "%s"}' % tmp_path,
                        )
                    ]
                )
            )
        ],
        # turn 3: real final answer
        [Chunk(Delta(content="It contains real.txt"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    final = agent.run("list my files")

    assert final == "It contains real.txt"
    nudges = [m for m in agent.messages if m["role"] == "user" and "Do not propose" in m["content"]]
    assert len(nudges) == 1
    tool_msgs = [m for m in agent.messages if m.get("role") == "tool"]
    assert "real.txt" in tool_msgs[0]["content"]


def test_no_nudge_for_normal_answers(tmp_path):
    turns = [[Chunk(Delta(content="Hello! How can I help?"))]]
    agent, ui = make_agent(tmp_path, turns)
    final = agent.run("hello")

    assert final == "Hello! How can I help?"
    assert not any(
        m["role"] == "user" and "Do not propose" in m.get("content", "")
        for m in agent.messages
    )


def test_nudge_only_once(tmp_path):
    turns = [
        [Chunk(Delta(content="```sh\nls\n```\nWant me to run it?"))],
        [Chunk(Delta(content="```sh\npwd\n```\nShall I run it?"))],
        [Chunk(Delta(content="done talking"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    agent.run("do it")
    nudges = [m for m in agent.messages if m["role"] == "user" and "Do not propose" in m["content"]]
    assert len(nudges) == 1
