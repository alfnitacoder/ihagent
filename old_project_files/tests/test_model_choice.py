"""Choosing a model by number, name, or saved .env line."""

from hfagent.config import (
    names_from_model_payload,
    resolve_model_choice,
    save_selected_model,
)


def test_names_from_ollama_and_openai_payloads():
    assert names_from_model_payload(
        {"models": [{"name": "qwen3:8b"}, {"model": "gpt-oss:20b"}]}
    ) == ["qwen3:8b", "gpt-oss:20b"]
    assert names_from_model_payload({"data": [{"id": "Qwen/Qwen3.8-27B"}]}) == [
        "Qwen/Qwen3.8-27B"
    ]


def test_resolve_by_number_exact_and_unique_part():
    names = ["gpt-oss:120b", "gpt-oss:20b", "kimi-k2.7-code"]
    assert resolve_model_choice("2", names) == "gpt-oss:20b"
    assert resolve_model_choice("gpt-oss:120b", names) == "gpt-oss:120b"
    assert resolve_model_choice("kimi", names) == "kimi-k2.7-code"
    try:
        resolve_model_choice("gpt-oss", names)
    except ValueError as exc:
        assert "1. gpt-oss:120b" in str(exc)
    else:
        raise AssertionError("ambiguous name should fail")


def test_save_selected_model_replaces_active_line_only(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "HF_BASE_URL=https://ollama.com/v1\n"
        "HFAGENT_MODEL=gpt-oss:120b\n"
        "# HFAGENT_MODEL=other\n",
        encoding="utf-8",
    )
    save_selected_model("gpt-oss:20b", path)
    text = path.read_text(encoding="utf-8")
    assert "HFAGENT_MODEL=gpt-oss:20b" in text
    assert "# HFAGENT_MODEL=other" in text
    assert text.count("HFAGENT_MODEL=gpt-oss:120b") == 0
