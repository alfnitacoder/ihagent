"""Vision-model settings and the OpenAI ``image_url`` request.

``HFAGENT_VISION_MODEL`` picks the model. ``HFAGENT_VISION_BASE_URL`` and
``HFAGENT_VISION_API_KEY`` are optional and default to the chat backend
the agent is using right now (Ollama Cloud, local Ollama, or a Kaggle or
Colab OpenAI-compatible ``/v1``). A text-only chat model such as
``gpt-oss:120b`` is not used as the vision model.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable


_VISION_MARKERS = (
    "gpt-4o",
    "gpt-4.1",
    "claude",
    "gemini",
    "llava",
    "pixtral",
    "vision",
    "gemma3",
    "moondream",
    "minicpm-v",
    "internvl",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5vl",
    "qwen2.5-vl",
    "llama3.2-vision",
)

_chat_provider: Callable[[], tuple[str, str, str]] | None = None


def bind_chat(provider: Callable[[], tuple[str, str, str]]) -> None:
    """Remember how to read the live chat host, model, and key."""
    global _chat_provider
    _chat_provider = provider


def current_chat() -> tuple[str, str, str]:
    """``(base_url, model, api_key)`` for the active chat backend."""
    if _chat_provider is not None:
        try:
            base, model, key = _chat_provider()
            return str(base or ""), str(model or ""), str(key or "")
        except Exception:
            pass
    base = (os.environ.get("HF_BASE_URL") or "").strip() or "https://router.huggingface.co/v1"
    model = (os.environ.get("HFAGENT_MODEL") or "").strip()
    key = (os.environ.get("HFAGENT_API_KEY") or "").strip()
    if not key:
        try:
            from ..config import api_key_info

            key = api_key_info(base)[0]
        except Exception:
            key = ""
    return base, model, key


def model_can_see(model: str) -> bool:
    """True for Ollama vision tags and other multimodal chat models.

    ``gpt-oss:120b`` and the default Qwen coder are text-only.
    ``qwen2.5vl``, ``gemma3``, and ``llama3.2-vision`` can see.
    """
    blob = (model or "").strip().lower()
    if not blob:
        return False
    if any(marker in blob for marker in _VISION_MARKERS):
        return True
    return blob.endswith("vl") or bool(re.search(r"(?:^|[^a-z0-9])vl(?:[^a-z0-9]|$)", blob))


@dataclass(frozen=True)
class VisionSettings:
    model: str
    base_url: str
    api_key: str
    chat_model: str
    chat_base_url: str
    model_source: str
    url_source: str
    key_source: str


def resolve_vision(
    chat_base: str = "",
    chat_model: str = "",
    chat_key: str = "",
    env: dict[str, str] | None = None,
) -> VisionSettings:
    """Apply env overrides, then fall back to the current chat backend."""
    source = os.environ if env is None else env
    model = (source.get("HFAGENT_VISION_MODEL") or "").strip()
    base = (source.get("HFAGENT_VISION_BASE_URL") or "").strip()
    key = (source.get("HFAGENT_VISION_API_KEY") or "").strip()
    model_source = "HFAGENT_VISION_MODEL" if model else "default"
    url_source = "HFAGENT_VISION_BASE_URL" if base else "current backend"
    key_source = "HFAGENT_VISION_API_KEY" if key else "current backend"
    if not model and model_can_see(chat_model):
        model = chat_model
        model_source = "current model"
    if not base:
        base = chat_base
    if not key:
        key = chat_key
    return VisionSettings(
        model=model,
        base_url=base,
        api_key=key,
        chat_model=chat_model,
        chat_base_url=chat_base,
        model_source=model_source,
        url_source=url_source,
        key_source=key_source,
    )


def vision_setup_message(chat_model: str = "") -> str:
    """How to point a text-only chat model at a separate vision model."""
    shown = chat_model.strip() or "the current chat model"
    return (
        f"Error: no vision model is configured, and {shown} cannot see images. "
        "The chat model stays as it is (Ollama Cloud gpt-oss:120b and other "
        "text models are not asked to look). Set a vision model on the same "
        "OpenAI-compatible /v1 host, or point at another one:\n"
        "  HFAGENT_VISION_MODEL=qwen2.5vl\n"
        "  HFAGENT_VISION_BASE_URL=http://127.0.0.1:11434/v1"
        "   # optional; defaults to the current host\n"
        "  HFAGENT_VISION_API_KEY=..."
        "   # optional; defaults to the current key\n"
        "Ollama tags that work: qwen2.5vl, gemma3, llama3.2-vision. "
        "On Ollama Cloud, use a Qwen-VL tag. A Kaggle or Colab Ollama server "
        "is the same kind of /v1 URL. Images are sent as OpenAI image_url "
        "data URLs."
    )


def locate_prompt(description: str, width: int, height: int) -> str:
    return (
        "You are locating one target in a screenshot for a desktop agent. "
        f"The image is {width} pixels wide and {height} pixels tall. "
        "The origin is the top-left pixel of this image, not the desktop. "
        f"Target: {description}\n"
        'Reply with JSON only: {"found": true, "x": <number>, "y": <number>}\n'
        "x and y are pixels in this image. "
        'If the target is not visible, reply {"found": false, "reason": "..."}.'
    )


def describe_prompt(question: str) -> str:
    return (
        "You are looking at one screenshot of the user's focused window. "
        "Describe only what is visible, in plain text. "
        f"Question: {question}"
    )


def build_image_messages(prompt: str, png: bytes) -> list[dict[str, Any]]:
    """OpenAI chat content. Ollama's ``/v1`` endpoint accepts this shape."""
    encoded = base64.standard_b64encode(png).decode("ascii")
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                },
            ],
        }
    ]


def parse_point(text: str, pixel_width: int, pixel_height: int) -> tuple[float, float] | str:
    """Pixel coordinates, or an ``Error:`` string.

    Values in ``0..1`` that use a decimal (``0.5``) are fractions of the
    image. Integer pixels are left alone, so ``(1, 1)`` stays the pixel
    next to the origin.
    """
    raw = (text or "").strip()
    if not raw:
        return "Error: the vision model returned an empty location."
    payload = _extract_json(raw)
    if not isinstance(payload, dict):
        return (
            "Error: the vision model did not return JSON coordinates. "
            f"It said: {raw[:180]}"
        )
    if payload.get("found") is False:
        reason = str(payload.get("reason") or "not found").strip()
        return f"Error: the vision model could not find the target ({reason})."
    try:
        x = float(payload["x"])
        y = float(payload["y"])
    except (KeyError, TypeError, ValueError):
        return f"Error: the vision model JSON had no x and y. It said: {raw[:180]}"
    if _is_fraction(payload, x, y) and pixel_width > 1 and pixel_height > 1:
        x *= pixel_width
        y *= pixel_height
    if x < 0 or y < 0 or x > pixel_width or y > pixel_height:
        return (
            f"Error: vision coordinates ({_trim(x)}, {_trim(y)}) are outside "
            f"the screenshot ({pixel_width}x{pixel_height})."
        )
    return (x, y)


def openai_complete(settings: VisionSettings, messages: list[dict[str, Any]]) -> str:
    """One non-streaming chat completion. Tests inject a replacement."""
    from openai import OpenAI

    client = OpenAI(
        api_key=settings.api_key or "none",
        base_url=settings.base_url or None,
        timeout=60,
        max_retries=1,
    )
    response = client.chat.completions.create(
        model=settings.model,
        messages=messages,
        temperature=0,
    )
    content = response.choices[0].message.content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(getattr(item, "text", "") or item))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _extract_json(text: str) -> dict[str, Any] | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    blob = fenced.group(1) if fenced else text
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(blob[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _is_fraction(payload: dict[str, Any], x: float, y: float) -> bool:
    if not (0 <= x <= 1 and 0 <= y <= 1):
        return False
    if x != int(x) or y != int(y):
        return True
    raw = json.dumps(payload)
    return bool(re.search(r'"x"\s*:\s*\d+\.\d+|"y"\s*:\s*\d+\.\d+', raw))


def _trim(value: float) -> str:
    if abs(value - round(value)) < 0.001:
        return str(int(round(value)))
    return f"{value:.2f}"
