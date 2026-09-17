"""Tests for the propose-vs-act nudge in the agent loop."""

import json

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


def test_remote_task_status_line(tmp_path):
    from tests.test_agent import make_agent as _mk

    turns = [
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="c1",
                            name="ssh_run",
                            arguments='{"host": "kali219", "command": "ls /root"}',
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content="listed /root on kali219"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    ui.statuses = []

    def capture_status(text):
        ui.statuses.append(text)

    ui.on_status = capture_status
    agent.run("list /root on kali219")
    assert any("remote task on kali219" in s for s in ui.statuses)


def test_prompt_has_persistence_rule():
    from hfagent.prompts import SYSTEM_PROMPT

    assert "Never abandon a task after a failure" in SYSTEM_PROMPT
    assert "until the" in SYSTEM_PROMPT and "complete" in SYSTEM_PROMPT


def test_midtask_announcement_gets_nudged_then_acts(tmp_path):
    """Regression: after a failed scan, model plans 'nslookup' but must RUN it."""
    turns = [
        # turn 1: the nmap scan runs and fails to resolve
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="c1",
                            name="ssh_run",
                            arguments='{"host": "kali219", "command": "nmap sims.pn.cu"}',
                        )
                    ]
                )
            )
        ],
        # turn 2: announces the next step without acting (this command will...)
        [
            Chunk(
                Delta(
                    content=(
                        "The scan failed to resolve the domain. "
                        "This command will attempt to resolve it with nslookup."
                    )
                )
            )
        ],
        # turn 3 (after nudge): actually runs nslookup
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="c2",
                            name="ssh_run",
                            arguments='{"host": "kali219", "command": "nslookup sims.pn.cu"}',
                        )
                    ]
                )
            )
        ],
        # turn 4: final report
        [Chunk(Delta(content="DNS does not resolve sims.pn.cu - that is the blocker."))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    ui.statuses = []
    ui.on_status = lambda text: ui.statuses.append(text)
    final = agent.run("use kali219 scan the sims")

    assert final.startswith("DNS does not resolve")
    assert len(ui.actions) == 2  # nmap + nslookup both ran
    assert any("nudging" in s for s in ui.statuses)


def test_second_proposal_nudge_quotes_announcement(tmp_path):
    """Regression: repeated 'I will check...' announcements must escalate."""
    announce = "I will check if the wordlist exists on kali219."
    turns = [
        [Chunk(Delta(content=announce))],
        [Chunk(Delta(content=announce))],  # identical stall after 1st nudge
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="c1",
                            name="ssh_run",
                            arguments='{"host": "kali219", "command": "ls /usr/share/wordlists/"}',
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content="wordlists directory listed"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    ui.statuses = []
    ui.on_status = lambda text: ui.statuses.append(text)
    final = agent.run("check the wordlist")

    assert final == "wordlists directory listed"
    escalated = [
        m for m in agent.messages
        if m["role"] == "user" and "You announced:" in m["content"]
    ]
    assert len(escalated) == 1
    assert announce[:40] in escalated[0]["content"]


def test_proposal_rollback_gives_tip(tmp_path):
    turns = [
        [Chunk(Delta(content="I will check the wordlist."))] for _ in range(4)
    ]
    agent, ui = make_agent(tmp_path, turns)
    base_len = len(agent.messages)
    final = agent.run("check the wordlist")

    assert final == ""
    assert len(agent.messages) == base_len
    assert any("Tip:" in e for e in ui.errors)


def test_empty_final_after_tools_returns_note_not_none(tmp_path):
    """Model ends with empty text after work: store '' and say (done)."""
    turns = [
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0, id="c1", name="list_dir", arguments="{}"
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content=""))],  # empty final answer
        [Chunk(Delta(content=""))],  # bounces twice, then gives up
        [Chunk(Delta(content=""))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    final = agent.run("list files")

    assert "(done" in final
    last_assistant = [
        m for m in agent.messages if m.get("role") == "assistant"
    ][-1]
    assert last_assistant["content"] == ""


def test_none_content_history_is_sanitized_for_ollama(tmp_path):
    """Poisoned session history (assistant content None, no tool_calls)
    must be scrubbed before every API call - Ollama 400s otherwise."""
    turns = [[Chunk(Delta(content="recovered fine"))]]
    agent, ui = make_agent(tmp_path, turns)
    agent.messages.append({"role": "assistant", "content": None})
    agent.messages.append({"role": "user", "content": "hello"})
    final = agent.run("hello")

    assert final == "recovered fine"
    sent = agent.client.chat.completions.calls[-1]["messages"]
    bad = [
        m for m in sent
        if m.get("role") == "assistant"
        and m.get("content") is None
        and not m.get("tool_calls")
    ]
    assert not bad


def test_approval_auto_skips_prompts(tmp_path):
    turns = [
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="c1",
                            name="write_file",
                            arguments=json.dumps(
                                {
                                    "path": str(tmp_path / "x.txt"),
                                    "content": "hi",
                                }
                            ),
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content="written"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    agent.config.approval = "auto"
    target = tmp_path / "x.txt"
    agent.run(f"write {target}")
    assert ui.approvals == []  # no prompt in auto mode
    assert target.exists()


def test_approval_default_asks(tmp_path):
    turns = [
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="c1",
                            name="write_file",
                            arguments=json.dumps(
                                {
                                    "path": str(tmp_path / "x.txt"),
                                    "content": "hi",
                                }
                            ),
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content="written"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    agent.config.approval = "default"
    ui.allow = True
    agent.run(f"write {tmp_path / 'x.txt'}")
    assert len(ui.approvals) == 1


def test_auto_mode_nudges_more_persistently(tmp_path):
    """In auto mode the agent keeps demanding action (6 nudges, not 2)."""
    announce = "I will check the target now."
    turns = (
        [[Chunk(Delta(content=announce))] for _ in range(5)]
        + [
            [
                Chunk(
                    Delta(
                        tool_calls=[
                            ToolCallDelta(
                                0,
                                id="c1",
                                name="ssh_run",
                                arguments='{"host": "kali219", "command": "id"}',
                            )
                        ]
                    )
                )
            ]
        ]
        + [[Chunk(Delta(content="checked"))]]
    )
    agent, ui = make_agent(tmp_path, turns)
    ui.statuses = []
    ui.on_status = lambda text: ui.statuses.append(text)
    agent.config.approval = "auto"
    final = agent.run("check the target")

    assert final == "checked"
    assert len(ui.actions) == 1
    assert sum("(auto)" in s for s in ui.statuses) == 5  # 5 nudges, all auto


def test_default_mode_hands_back_midtask_after_two(tmp_path):
    turns = (
        [
            [
                Chunk(
                    Delta(
                        tool_calls=[
                            ToolCallDelta(
                                0,
                                id="c1",
                                name="ssh_run",
                                arguments='{"host": "kali219", "command": "id"}',
                            )
                        ]
                    )
                )
            ]
        ]
        + [[Chunk(Delta(content="I will check the target now."))] for _ in range(3)]
    )
    agent, ui = make_agent(tmp_path, turns)
    ui.statuses = []
    ui.on_status = lambda text: ui.statuses.append(text)
    agent.config.approval = "default"
    final = agent.run("check the target")

    assert final == "I will check the target now."
    assert any("say 'run it'" in s for s in ui.statuses)
    assert sum("(auto)" in s for s in ui.statuses) == 0


def test_auto_mode_beyond_limit_returns_plan(tmp_path):
    announce = "I will check the target now."
    turns = [[Chunk(Delta(content=announce))] for _ in range(8)]
    agent, ui = make_agent(tmp_path, turns)
    ui.statuses = []
    ui.on_status = lambda text: ui.statuses.append(text)
    agent.config.approval = "auto"
    final = agent.run("check the target")

    assert final == announce
    assert any("returning its plan" in s for s in ui.statuses)


def test_auto_mode_catches_increase_timeout_announcement(tmp_path):
    """Regression: 'I will increase the timeout and try again' must nudge in auto."""
    turns = [
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="c1",
                            name="ssh_run",
                            arguments='{"host": "kali219", "command": "nmap sims.pn.cu"}',
                        )
                    ]
                )
            )
        ],
        # mid-task: timed out, announces retry with a verb not in the list
        [
            Chunk(
                Delta(
                    content=(
                        "The scan timed out after 60 seconds. "
                        "I will increase the timeout and try again."
                    )
                )
            )
        ],
        # after nudge: retries properly with background mode
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="c2",
                            name="ssh_run",
                            arguments='{"host": "kali219", "command": "nmap sims.pn.cu", "background": true}',
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content="Scan running in background - will report results."))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    ui.statuses = []
    ui.on_status = lambda text: ui.statuses.append(text)
    agent.config.approval = "auto"
    final = agent.run("scan the sims")

    assert "running in background" in final
    assert len(ui.actions) == 2
    assert sum("(auto)" in s for s in ui.statuses) == 1


def test_default_mode_does_not_use_auto_intent_matching(tmp_path):
    """Default mode keeps the old verb-matched behavior for this phrase."""
    turns = [
        [
            Chunk(
                Delta(
                    content=(
                        "The scan timed out after 60 seconds. "
                        "I will increase the timeout and try again."
                    )
                )
            )
        ]
    ]
    agent, ui = make_agent(tmp_path, turns)
    agent.config.approval = "default"
    final = agent.run("scan the sims")
    assert "increase the timeout" in final
