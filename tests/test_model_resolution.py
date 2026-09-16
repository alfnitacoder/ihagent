"""Tests for Ollama model name resolution."""

import pytest

from hfagent import config


def test_exact_match(monkeypatch):
    monkeypatch.setattr(
        config, "_ollama_model_names",
        lambda: ["qwen2.5-coder:7b", "qwen2.5-coder-abliterated:latest"],
    )
    assert config._resolve_ollama_name("qwen2.5-coder:7b") == "qwen2.5-coder:7b"


def test_unique_substring(monkeypatch):
    monkeypatch.setattr(
        config, "_ollama_model_names",
        lambda: ["qwen2.5-coder:7b", "qwen2.5-coder-abliterated:latest"],
    )
    assert (
        config._resolve_ollama_name("abliterated")
        == "qwen2.5-coder-abliterated:latest"
    )


def test_ambiguous_match_raises(monkeypatch):
    monkeypatch.setattr(
        config, "_ollama_model_names",
        lambda: ["qwen2.5-coder:7b", "qwen2.5-coder-abliterated:latest"],
    )
    with pytest.raises(ValueError, match="no unique"):
        config._resolve_ollama_name("qwen2.5-coder")


def test_no_match_raises_lists_candidates(monkeypatch):
    monkeypatch.setattr(
        config, "_ollama_model_names", lambda: ["qwen2.5-coder:7b"]
    )
    with pytest.raises(ValueError, match="qwen2.5-coder:7b"):
        config._resolve_ollama_name("llama3")
