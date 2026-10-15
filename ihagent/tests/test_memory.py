"""Tests for persistent long-term memory."""

import pytest

from hfagent.memory import load_memory, save_fact
from hfagent.agent import system_prompt
from hfagent.tools import default_registry


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("HFAGENT_MEMORY_PATH", str(tmp_path / "mem"))


def test_save_and_load_fact():
    result = save_fact("kali219 is the user's Kali Linux pentest box")
    assert "remembered" in result
    assert "kali219" in load_memory()


def test_duplicate_fact_not_duplicated():
    save_fact("wantok20 is the main server")
    save_fact("wantok20 is the main server")
    assert load_memory().count("wantok20 is the main server") == 1


def test_fact_is_timestamped():
    save_fact("prefers root shells on lab boxes")
    assert "[2" in load_memory()


def test_system_prompt_includes_memory():
    save_fact("kali219 needs ProxyJump voipgw.noc")
    prompt = system_prompt()
    assert "ProxyJump voipgw.noc" in prompt
    assert "Persistent memory" in prompt


def test_system_prompt_without_memory():
    assert "Persistent memory" not in system_prompt()


def test_remember_tool_end_to_end():
    registry = default_registry()
    result = registry.get("remember").run(fact="voipgw.noc is the SSH jump host")
    assert "remembered" in result
    assert "voipgw.noc" in load_memory()
