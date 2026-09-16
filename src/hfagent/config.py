"""Environment-driven configuration for hfagent."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-32B-Instruct"
DEFAULT_BASE_URL = "https://router.huggingface.co/v1"
LOCAL_BASE_URL = "http://127.0.0.1:1234/v1"
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
    ) -> "Config":
        if local:
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
