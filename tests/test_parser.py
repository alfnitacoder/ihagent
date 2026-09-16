"""Tests for text-embedded tool call parsing."""

from hfagent.agent import parse_text_tool_calls


def test_tool_call_tag():
    calls, rest = parse_text_tool_calls(
        'Thought: I should look.\n<tool_call>\n{"name": "list_dir", "arguments": {"path": "/tmp"}}\n</tool_call>'
    )
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "list_dir"
    assert '"path": "/tmp"' in calls[0]["function"]["arguments"]
    assert "tool_call" not in rest
    assert "Thought" in rest


def test_short_tool_tag_ollama_style():
    calls, rest = parse_text_tool_calls(
        '<tool>\n{"name": "grep", "arguments": {"pattern": "def"}}\n</tool>'
    )
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "grep"


def test_plain_content_untouched():
    calls, rest = parse_text_tool_calls("Just a normal answer with {braces}.")
    assert calls == []
    assert "normal answer" in rest


def test_bad_json_ignored():
    calls, rest = parse_text_tool_calls('<tool_call>{not json}</tool_call>')
    assert calls == []


def test_fenced_json_block():
    calls, rest = parse_text_tool_calls(
        '```json\n{"name": "list_dir", "arguments": {"path": "/Users/x"}}\n```'
    )
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "list_dir"
    assert "list_dir" not in rest


def test_fenced_nested_json():
    calls, rest = parse_text_tool_calls(
        '```json\n{"name": "read_file", "arguments": {"path": "/a/b.py"}}\n```\nLet me check.'
    )
    assert len(calls) == 1
    assert rest.strip() == "Let me check."


def test_fenced_non_tool_json_ignored():
    calls, rest = parse_text_tool_calls(
        '```json\n{"config": true, "debug": false}\n```'
    )
    assert calls == []
