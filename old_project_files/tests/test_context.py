"""Context estimates and history compaction."""

import json

from hfagent.context import (
    SUMMARY_MARK,
    compact_messages,
    completion_budget,
    estimate_messages_tokens,
    estimate_text_tokens,
)
from tests.test_agent import Chunk, Delta, make_agent


def _assert_pairing(messages):
    pending: list[str] = []
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            assert not pending
            pending = [call["id"] for call in msg["tool_calls"]]
        elif msg.get("role") == "tool":
            assert msg["tool_call_id"] in pending
            pending.remove(msg["tool_call_id"])
        else:
            assert not pending
    assert not pending


def test_estimate_is_cheap_and_grows_with_text():
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens("a" * 400) == 100
    short = [{"role": "user", "content": "hi"}]
    long = [{"role": "user", "content": "hi" * 5000}]
    assert estimate_messages_tokens(long) > estimate_messages_tokens(short)


def test_default_budget_leaves_room_in_a_64k_window():
    budget = completion_budget(65536)
    assert 65536 - 8192 <= budget < 65536


def test_under_budget_is_unchanged():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    updated, info = compact_messages(messages, max_tokens=10_000)
    assert updated == messages
    assert info is None


def test_drops_old_turns_keeps_system_recent_and_tool_pairs():
    messages = [
        {"role": "system", "content": "SYSTEM_PROMPT_STAYS"},
        {"role": "user", "content": "ANCIENT " * 5000 + " ENDMARKER_SHOULD_DROP"},
        {"role": "assistant", "content": "ancient answer " * 2000},
        {"role": "user", "content": "use the tool"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "file body"},
    ]
    updated, info = compact_messages(messages, max_tokens=800, protect_tail=0)
    assert info is not None
    assert info["dropped_messages"] >= 1
    assert updated[0]["content"] == "SYSTEM_PROMPT_STAYS"
    blob = json.dumps(updated)
    assert "ENDMARKER_SHOULD_DROP" not in blob
    assert SUMMARY_MARK in blob
    assert any(m.get("role") == "tool" and m["tool_call_id"] == "c1" for m in updated)
    _assert_pairing(updated)
    # The tool result stays attached to its assistant call.
    tool_at = next(i for i, m in enumerate(updated) if m.get("role") == "tool")
    assert updated[tool_at - 1]["tool_calls"][0]["id"] == "c1"


def test_oversized_tool_group_is_dropped_together():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "keep-me-if-space"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c9",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c9", "content": "Z" * 100_000},
        {"role": "user", "content": "TAIL_QUESTION"},
    ]
    updated, info = compact_messages(messages, max_tokens=300, protect_tail=1)
    assert info is not None
    assert updated[-1]["content"] == "TAIL_QUESTION"
    assert updated[0]["content"] == "sys"
    assert not any(m.get("role") == "tool" for m in updated)
    assert not any(m.get("tool_calls") for m in updated)
    _assert_pairing(updated)


def test_current_exchange_survives_compaction_and_rollback(tmp_path):
    agent, ui = make_agent(tmp_path, [[Chunk(Delta(content=""))] for _ in range(3)])
    agent.config.context_tokens = 800
    agent.messages.append(
        {"role": "user", "content": "ANCIENT " * 4000 + " ENDMARKER_SHOULD_DROP"}
    )
    agent.messages.append({"role": "assistant", "content": "old answer " * 2000})
    agent.run("please answer this")

    assert any("empty responses" in err for err in ui.errors)
    assert any("context full" in status for status in ui.statuses)
    blob = json.dumps(agent.messages)
    assert "please answer this" not in blob
    assert "ENDMARKER_SHOULD_DROP" not in blob
    assert agent.messages[0]["role"] == "system"
    assert SUMMARY_MARK in blob
    _assert_pairing(agent.messages)


def test_agent_compacts_before_the_request(tmp_path):
    agent, ui = make_agent(tmp_path, [[Chunk(Delta(content="fresh"))]])
    agent.config.context_tokens = 800
    agent.messages.append(
        {"role": "user", "content": "ANCIENT " * 4000 + " ENDMARKER_SHOULD_DROP"}
    )
    agent.messages.append({"role": "assistant", "content": "prior"})
    final = agent.run("now?")
    assert final == "fresh"
    sent = json.dumps(agent.client.chat.completions.calls[0]["messages"])
    assert SUMMARY_MARK in sent
    assert "ENDMARKER_SHOULD_DROP" not in sent
    assert "now?" in sent
    assert any("context full" in status for status in ui.statuses)
