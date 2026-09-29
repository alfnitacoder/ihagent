"""Environment-driven configuration for hfagent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    """Load KEY=VALUE from .env files.

    Project ``./.env`` overrides the current process env (local run config).
    ``~/.hfagent/.env`` only fills keys that are still unset.
    """
    aliases = {
        "ollama_api_key": "OLLAMA_API_KEY",
        "hf_token": "HF_TOKEN",
        "hf_api_key": "HF_API_KEY",
        "hf_base_url": "HF_BASE_URL",
        "hfagent_model": "HFAGENT_MODEL",
    }

    def apply(path: Path, *, override: bool) -> None:
        if not path.is_file():
            return
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = aliases.get(key.strip().lower(), key.strip())
            value = value.strip().strip("'").strip('"')
            if not key:
                continue
            if override or key not in os.environ:
                os.environ[key] = value

    project = Path.cwd() / ".env"
    if not project.is_file():
        parent = Path.cwd().parent / ".env"
        if parent.is_file():
            project = parent
    apply(project, override=True)
    apply(Path.home() / ".hfagent" / ".env", override=False)

def _api_key_for(base_url: str) -> str:
    if "ollama.com" in base_url:
        return (
            os.environ.get("OLLAMA_API_KEY")
            or os.environ.get("HF_TOKEN")
            or os.environ.get("HF_API_KEY")
            or ""
        )
    return os.environ.get("HF_TOKEN") or os.environ.get("HF_API_KEY") or ""


def project_env_path() -> Path:
    """The project .env IHAgent is using, or ``./.env`` if none exists yet."""
    cwd = Path.cwd() / ".env"
    if cwd.is_file():
        return cwd
    parent = Path.cwd().parent / ".env"
    if parent.is_file():
        return parent
    return cwd


def save_selected_model(model: str, path: Path | None = None) -> Path:
    """Remember the chosen model in the project .env."""
    path = path or project_env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    replaced = False
    out: list[str] = []
    for line in lines:
        if not replaced and line.strip().startswith("HFAGENT_MODEL="):
            out.append(f"HFAGENT_MODEL={model}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1] != "":
            out.append("")
        out.append(f"HFAGENT_MODEL={model}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return path


def names_from_model_payload(payload: object) -> list[str]:
    """Pull model ids from an Ollama tags body or an OpenAI /v1/models body."""
    if not isinstance(payload, dict):
        return []
    names: list[str] = []
    entries = payload.get("models")
    if not isinstance(entries, list):
        entries = payload.get("data")
    if not isinstance(entries, list):
        return []
    for entry in entries:
        if isinstance(entry, str) and entry:
            names.append(entry)
            continue
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("model") or entry.get("id") or ""
        if isinstance(name, str) and name:
            names.append(name)
    return names


def list_model_names(base_url: str, api_key: str = "") -> list[str]:
    """List models on the current local Ollama, Ollama Cloud, or OpenAI-compatible host."""
    import json
    from urllib.request import Request, urlopen

    host = base_url.rstrip("/")
    urls: list[str] = []
    if "11434" in host or "ollama.com" in host:
        origin = host[:-3] if host.endswith("/v1") else host
        urls.append(origin.rstrip("/") + "/api/tags")
    urls.append(host + "/models")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    for url in urls:
        try:
            with urlopen(Request(url, headers=headers), timeout=8) as resp:
                payload = json.load(resp)
        except Exception:
            continue
        names = names_from_model_payload(payload)
        if names:
            return names
    return []


def resolve_model_choice(requested: str, names: list[str]) -> str:
    """Pick a model by exact name, unique substring, or 1-based list number."""
    requested = requested.strip()
    if not names:
        if not requested:
            raise ValueError("no models available")
        return requested
    if requested.isdigit():
        index = int(requested)
        if 1 <= index <= len(names):
            return names[index - 1]
        raise ValueError(f"model number {requested} is out of range (1-{len(names)})")
    if requested in names:
        return requested
    matches = [name for name in names if requested.lower() in name.lower()]
    if len(matches) == 1:
        return matches[0]
    if not requested:
        return names[0]
    shown = "\n".join(f"{i}. {name}" for i, name in enumerate(names, 1))
    raise ValueError(
        f"no unique model match for '{requested}'.\n{shown}"
    )


def _ollama_model_names() -> list[str]:
    import json
    from urllib import request

    try:
        with request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as resp:
            tags = json.load(resp)
        return [m["name"] for m in tags.get("models", [])]
    except Exception:
        return []


def _resolve_ollama_name(requested: str) -> str:
    """Resolve a model name with exact-first, then unique-substring matching."""
    return resolve_model_choice(requested, _ollama_model_names())


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
    approval: str = "default"  # 'default' (ask) or 'auto' (no prompts)
    temperature: float = 0.0

    @classmethod
    def load(
        cls,
        model: str | None = None,
        base_url: str | None = None,
        max_steps: int | None = None,
        temperature: float | None = None,
        local: bool = False,
        ollama: str | None = None,
        approval: str | None = None,
    ) -> "Config":
        _load_dotenv()
        if ollama is not None:
            base_url = OLLAMA_BASE_URL
            requested = ollama or os.environ.get("HFAGENT_LOCAL_MODEL") or ""
            model = _resolve_ollama_name(requested) if requested else _first_ollama_model()
        elif local:
            base_url = base_url or LOCAL_BASE_URL
            model = os.path.expanduser(
                model or os.environ.get("HFAGENT_LOCAL_MODEL", DEFAULT_LOCAL_MODEL)
            )
        resolved_base = base_url or os.environ.get("HF_BASE_URL", DEFAULT_BASE_URL)
        return cls(
            api_key=_api_key_for(resolved_base),
            base_url=resolved_base,
            model=model or os.environ.get("HFAGENT_MODEL", DEFAULT_MODEL),
            max_steps=max_steps or int(os.environ.get("HFAGENT_MAX_STEPS", "25")),
            approval=approval
            or os.environ.get("HFAGENT_APPROVAL", "")
            or ("auto" if os.environ.get("HFAGENT_YOLO", "") == "1" else "default"),
            temperature=temperature or 0.0,
        )
