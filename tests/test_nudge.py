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


def test_proposal_nudges_capped_at_two(tmp_path):
    turns = [
        [Chunk(Delta(content="```sh\nls\n```\nWant me to run it?"))],
        [Chunk(Delta(content="```sh\npwd\n```\nShall I run it?"))],
        [Chunk(Delta(content="```sh\nwhoami\n```\nShould I run it?"))],
        [Chunk(Delta(content="done talking"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    base_len = len(agent.messages)  # system only
    final = agent.run("do it")

    # after 2 nudges the exchange is rolled back clean
    assert final == ""
    assert len(agent.messages) == base_len
    assert len(ui.errors) == 1 and "rolled back" in ui.errors[0]


def test_nudge_fires_on_announced_intent(tmp_path):
    """'I'll list the network interfaces...' with no action -> nudge."""
    (tmp_path / "f.txt").write_text("x\n")
    turns = [
        [Chunk(Delta(content="I'll list the network interfaces and then extract the IP address."))],
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0, id="c1", name="list_dir",
                            arguments='{"path": "%s"}' % tmp_path,
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content="done: found f.txt"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    final = agent.run("what is my wifi IP?")
    assert final == "done: found f.txt"
    assert any(
        m["role"] == "user" and "Do not propose" in m["content"]
        for m in agent.messages
    )


def test_no_nudge_after_tools_were_used(tmp_path):
    """'Let me show you...' after real tool use is a legitimate summary."""
    turns = [
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(0, id="c1", name="list_dir", arguments="{}")
                    ]
                )
            )
        ],
        [Chunk(Delta(content="Listed. Let me show you the summary: it works."))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    agent.run("list files")
    assert not any(
        m["role"] == "user" and "Do not propose" in m.get("content", "")
        for m in agent.messages
    )


def test_no_nudge_for_let_me_know(tmp_path):
    turns = [[Chunk(Delta(content="Done! Let me know if you need anything else."))]]
    agent, ui = make_agent(tmp_path, turns)
    agent.run("ok")
    assert not any(
        m["role"] == "user" and "Do not propose" in m.get("content", "")
        for m in agent.messages
    )


def test_fabricated_tool_response_is_stripped_and_nudged(tmp_path):
    """Model fakes '<tool_response>exit code: 0...</tool_response>' -> strip + nudge."""
    turns = [
        [
            Chunk(
                Delta(
                    content=(
                        "<tool_response>\nexit code: 0\n192.168.1.100\n"
                        "</tool_response>"
                    )
                )
            )
        ],
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="c1",
                            name="run_command",
                            arguments='{"command": "curl -s ifconfig.me"}',
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content="Your public IP is in the output above."))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    final = agent.run("what is my IP?")

    assert final == "Your public IP is in the output above."
    # fabricated block stripped from stored history
    first_assistant = next(
        m for m in agent.messages if m.get("role") == "assistant"
    )
    assert "tool_response" not in (first_assistant.get("content") or "")
    # targeted nudge sent exactly once
    nudges = [
        m
        for m in agent.messages
        if m["role"] == "user" and "Never write tool_response tags" in m["content"]
    ]
    assert len(nudges) == 1
    # the real tool ran afterwards
    assert any(m.get("role") == "tool" for m in agent.messages)


def test_nudge_fires_on_recipe_mode(tmp_path):
    """'To list the files... you can run:' announces without acting."""
    (tmp_path / "f.txt").write_text("x\n")
    turns = [
        [Chunk(Delta(content="To list the files in your current directory, you can run the following command:\n```sh\nls\n```"))],
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0, id="c1", name="list_dir",
                            arguments='{"path": "%s"}' % tmp_path,
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content="found f.txt"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    final = agent.run("list my files in this folder")
    assert final == "found f.txt"
    assert len(ui.actions) == 1


def test_nudge_fires_on_lets_install(tmp_path):
    """Regression: 'Let's install X on <host>' announced without acting."""
    turns = [
        [Chunk(Delta(content="Let's install `mitmproxy` on `kali219`."))],
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0, id="c1", name="run_command",
                            arguments='{"command": "apt install -y mitmproxy"}',
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content="installed"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    final = agent.run("run apt install mitmproxy")
    assert final == "installed"
    assert len(ui.actions) == 1


def test_no_nudge_for_long_explanation_starting_with_lets(tmp_path):
    body = (
        "Let's start with the basics. DNS translates human-readable names "
        + "into IP addresses through a hierarchy of servers. "
        + "A recursive resolver asks the root servers, then the TLD servers, "
        + "then the authoritative servers for the domain, and each layer "
        + "caches results according to their TTL so future lookups are faster "
        + "and cheaper for everyone involved in the lookup chain."
    )
    turns = [[Chunk(Delta(content=body))]]
    agent, ui = make_agent(tmp_path, turns)
    agent.run("explain DNS")
    assert not any(
        m["role"] == "user" and "Do not propose" in m.get("content", "")
        for m in agent.messages
    )
