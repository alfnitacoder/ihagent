"""Where the model is running, and how many tokens the last call used."""

from __future__ import annotations

import json
from urllib.parse import urlparse
from urllib.request import urlopen


def device_from_vram(size: int, size_vram: int) -> str:
    """Classify an Ollama model from its RAM and VRAM footprint."""
    if size_vram <= 0:
        return "CPU"
    if size > 0 and size_vram < int(size * 0.9):
        return "GPU+CPU"
    return "GPU"


def infer_device(base_url: str, model: str) -> str | None:
    """Return GPU, CPU, GPU+CPU, or remote. None if it is not known yet."""
    if "11434" in base_url:
        return ollama_device(base_url, model)
    if "endpoints.huggingface.cloud" in base_url:
        return "GPU"
    if "huggingface.co" in base_url or "ollama.com" in base_url:
        return "remote"
    return None


def ollama_device(base_url: str, model: str, timeout: float = 1.5) -> str | None:
    """Ask the local Ollama daemon which processor has this model loaded."""
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    try:
        with urlopen(origin + "/api/ps", timeout=timeout) as resp:
            payload = json.load(resp)
    except Exception:
        return None
    wanted = model.strip().lower()
    for entry in payload.get("models") or []:
        name = str(entry.get("name") or entry.get("model") or "").lower()
        if wanted and wanted not in name and name not in wanted:
            continue
        return device_from_vram(
            int(entry.get("size") or 0),
            int(entry.get("size_vram") or 0),
        )
    return None
