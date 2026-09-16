"""Environment-driven configuration for hfagent."""

from __future__ import annotations

import os
from dataclasses import dataclass

def _first_ollama_model() -> str:
    """Return the first installed Ollama model, or '' if the server is unreachable."""
    import json
    from urllib import request

    try:
        with request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as resp:
            tags = json.load(resp)
        models = sorted(
            tags.get("models", []),
            key=lambda m: m.get("modified_at", ""),
            reverse=True,
        )
        return models[0]["name"] if models else ""
    except Exception:
        return ""


DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-32B-Instruct"
DEFAULT_BASE_URL = "https://router.huggingface.co/v1"
LOCAL_BASE_URL = "http://127.0.0.1:1234/v1"
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_LOCAL_MODEL = (
    "~/.cache/huggingface/mlx/qwen2.5-0.5b-4bit"
)


@dataclass
class Config:
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    max_steps: int = 25
    max_tool_result_chars: int = 40_000
    auto_approve: bool = False
    temperature: float = 0.0

    @classmethod
    def load(
        cls,
        model: str | None = None,
        base_url: str | None = None,
        max_steps: int | None = None,
        auto_approve: bool = False,
        temperature: float | None = None,
        local: bool = False,
        ollama: str | None = None,
    ) -> "Config":
        if ollama is not None:
            base_url = OLLAMA_BASE_URL
            model = ollama or os.environ.get("HFAGENT_LOCAL_MODEL") or _first_ollama_model()
        elif local:
            base_url = base_url or LOCAL_BASE_URL
            model = os.path.expanduser(
                model or os.environ.get("HFAGENT_LOCAL_MODEL", DEFAULT_LOCAL_MODEL)
            )
        return cls(
            api_key=os.environ.get("HF_TOKEN") or os.environ.get("HF_API_KEY") or "",
            base_url=base_url or os.environ.get("HF_BASE_URL", DEFAULT_BASE_URL),
            model=model or os.environ.get("HFAGENT_MODEL", DEFAULT_MODEL),
            max_steps=max_steps or int(os.environ.get("HFAGENT_MAX_STEPS", "25")),
            auto_approve=auto_approve or os.environ.get("HFAGENT_YOLO", "") == "1",
            temperature=temperature or 0.0,
        )
