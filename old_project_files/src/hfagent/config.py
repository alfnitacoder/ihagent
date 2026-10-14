"""Environment-driven configuration for hfagent.

Precedence, highest first:

1. Command-line flags (this invocation).
2. Process environment — variables exported in the shell. A value that is
   only there because the shell sourced ``~/.hfagent/hf.env`` does not count;
   it is attributed to that file and loses to the files below.
3. Project ``.env`` (``./.env``, or ``../.env`` when the current directory
   has none).
4. User file ``~/.hfagent/.env``.
5. User file ``~/.hfagent/hf.env`` (also read directly, so it applies even
   when the shell did not source it).
6. Built-in defaults.

``HFAGENT_API_KEY``, when set, is the key for every base URL and beats the
provider-specific variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .context import DEFAULT_CONTEXT_TOKENS


_ALIASES = {
    "ollama_api_key": "OLLAMA_API_KEY",
    "hf_token": "HF_TOKEN",
    "hf_api_key": "HF_API_KEY",
    "hf_base_url": "HF_BASE_URL",
    "hfagent_model": "HFAGENT_MODEL",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "hfagent_api_key": "HFAGENT_API_KEY",
    "hfagent_context_tokens": "HFAGENT_CONTEXT_TOKENS",
    "hfagent_max_steps": "HFAGENT_MAX_STEPS",
    "hfagent_approval": "HFAGENT_APPROVAL",
    "hfagent_yolo": "HFAGENT_YOLO",
    "hfagent_fallback_base_url": "HFAGENT_FALLBACK_BASE_URL",
    "hfagent_fallback_model": "HFAGENT_FALLBACK_MODEL",
    "hfagent_fallback_api_key": "HFAGENT_FALLBACK_API_KEY",
    "hfagent_local_model": "HFAGENT_LOCAL_MODEL",
    "hfagent_cloud_model": "HFAGENT_CLOUD_MODEL",
    "hfagent_browser_headless": "HFAGENT_BROWSER_HEADLESS",
    "hfagent_browser_profile": "HFAGENT_BROWSER_PROFILE",
    "hfagent_browser_download_dir": "HFAGENT_BROWSER_DOWNLOAD_DIR",
    "hfagent_browser_channel": "HFAGENT_BROWSER_CHANNEL",
    "hfagent_browser_executable": "HFAGENT_BROWSER_EXECUTABLE",
    "hfagent_browser_timeout_ms": "HFAGENT_BROWSER_TIMEOUT_MS",
    "hfagent_browser_max_chars": "HFAGENT_BROWSER_MAX_CHARS",
    "hfagent_iheye": "HFAGENT_IHEYE",
    "hfagent_iheye_track": "HFAGENT_IHEYE_TRACK",
    "hfagent_iheye_summary": "HFAGENT_IHEYE_SUMMARY",
    "hfagent_iheye_skin": "HFAGENT_IHEYE_SKIN",
    "hfagent_iheye_look": "HFAGENT_IHEYE_LOOK",
}

# key -> (value in the process before we wrote it, value we wrote)
_INJECTED: dict[str, tuple[str | None, str]] = {}
_LAYER_SOURCES: dict[str, str] = {}


def _reset_env_layer_state() -> None:
    """Undo values this process wrote from env files. Used by tests."""
    _undo_injected()
    _LAYER_SOURCES.clear()


def _undo_injected() -> None:
    for key, (prev, written) in list(_INJECTED.items()):
        current = os.environ.get(key)
        if current == written:
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
    _INJECTED.clear()


def _inject(key: str, value: str) -> None:
    prev = os.environ.get(key)
    if prev == value:
        return
    os.environ[key] = value
    _INJECTED[key] = (prev, value)


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        key, _, value = line.partition("=")
        key = _ALIASES.get(key.strip().lower(), key.strip())
        value = value.strip().strip("'").strip('"')
        if key and value:
            found[key] = value
    return found


def resolve_from_layers(
    name: str,
    process: dict[str, str],
    project: dict[str, str],
    user: dict[str, str],
    hfenv: dict[str, str],
) -> tuple[str | None, str]:
    """Pick a value and name the source that won.

    A process value that exactly matches ``~/.hfagent/hf.env`` is treated as
    that file (the shell sourced it), so project and user env files override it.
    """
    proc = process.get(name)
    hf_value = hfenv.get(name)
    sourced_hfenv = proc is not None and hf_value is not None and proc == hf_value
    if proc is not None and not sourced_hfenv:
        return proc, "process environment"
    if name in project:
        return project[name], "project .env"
    if name in user:
        return user[name], "~/.hfagent/.env"
    if hf_value is not None:
        return hf_value, "~/.hfagent/hf.env"
    if proc is not None:
        return proc, "process environment"
    return None, "default"


def apply_layered_env() -> dict[str, str]:
    """Apply the precedence above to os.environ. Returns key → source."""
    global _LAYER_SOURCES
    _undo_injected()
    process = dict(os.environ)
    project = _parse_env_file(project_env_path())
    user = _parse_env_file(user_env_path())
    hfenv = _parse_env_file(hf_env_path())
    sources: dict[str, str] = {}
    for key in set(project) | set(user) | set(hfenv):
        value, source = resolve_from_layers(key, process, project, user, hfenv)
        sources[key] = source
        if value is None or source == "process environment":
            continue
        _inject(key, value)
    _LAYER_SOURCES = sources
    return sources


def _load_dotenv() -> None:
    """Apply env-file precedence. See the module docstring."""
    apply_layered_env()


def source_of(name: str) -> str:
    if name in _LAYER_SOURCES:
        return _LAYER_SOURCES[name]
    if os.environ.get(name):
        return "process environment"
    return "default"


def _from_env(name: str, default: str) -> tuple[str, str]:
    value = os.environ.get(name)
    if value:
        return value, source_of(name)
    return default, "default"


def _from_env_int(name: str, default: int, cli_value: int | None) -> tuple[int, str]:
    if cli_value is not None:
        return int(cli_value), "command line"
    raw = os.environ.get(name)
    if raw:
        try:
            return int(raw), source_of(name)
        except ValueError:
            return default, "default"
    return default, "default"


def api_key_info(base_url: str) -> tuple[str, str]:
    """Return ``(key, env var)`` for this host.

    ``HFAGENT_API_KEY`` wins for every base URL when it is set. Otherwise the
    provider variable is used. Custom hosts suggest ``HFAGENT_API_KEY`` when
    nothing is set, so a stale ``HF_TOKEN`` is not the only place to look.
    """
    generic = (os.environ.get("HFAGENT_API_KEY") or "").strip()
    if generic:
        return generic, "HFAGENT_API_KEY"
    if "api.anthropic.com" in base_url:
        return (os.environ.get("ANTHROPIC_API_KEY") or "").strip(), "ANTHROPIC_API_KEY"
    if "ollama.com" in base_url:
        for name in ("OLLAMA_API_KEY", "HF_TOKEN", "HF_API_KEY"):
            value = (os.environ.get(name) or "").strip()
            if value:
                return value, name
        return "", "OLLAMA_API_KEY"
    for name in ("HF_TOKEN", "HF_API_KEY"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value, name
    return "", "HFAGENT_API_KEY"


def _api_key_for(base_url: str) -> str:
    return api_key_info(base_url)[0]


def project_env_path() -> Path:
    """The project .env IHAgent is using, or ``./.env`` if none exists yet."""
    cwd = Path.cwd() / ".env"
    if cwd.is_file():
        return cwd
    parent = Path.cwd().parent / ".env"
    if parent.is_file():
        return parent
    return cwd


def user_env_path() -> Path:
    """Personal settings. ``/url`` writes ``HF_BASE_URL`` here."""
    return Path.home() / ".hfagent" / ".env"


def hf_env_path() -> Path:
    """Profile written by ``scripts/setup-huggingface.sh`` and often sourced."""
    return Path.home() / ".hfagent" / "hf.env"


def env_file_has_key(path: Path, name: str) -> bool:
    """True when an active (non-comment) KEY= line is present."""
    if not path.is_file():
        return False
    prefix = f"{name}="
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(line.strip().startswith(prefix) for line in lines)


def _set_env_line(name: str, value: str, path: Path | None = None) -> Path:
    """Replace the first active KEY= line, or append it. Commented lines stay."""
    path = path or project_env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    prefix = f"{name}="
    replaced = False
    out: list[str] = []
    for line in lines:
        if not replaced and line.strip().startswith(prefix):
            out.append(f"{name}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1] != "":
            out.append("")
        out.append(f"{name}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return path


def save_selected_model(model: str, path: Path | None = None) -> Path:
    """Remember the chosen model in the project .env."""
    return _set_env_line("HFAGENT_MODEL", model, path)


def save_base_url(base_url: str, path: Path | None = None) -> Path:
    """Remember the active host in the project .env."""
    return _set_env_line("HF_BASE_URL", base_url, path)


def save_user_setting(name: str, value: str, path: Path | None = None) -> Path:
    """Remember a setting in ``~/.hfagent/.env``."""
    return _set_env_line(name, value, path or user_env_path())


def normalize_base_url(raw: str) -> str:
    """Accept a bare tunnel host and append ``/v1`` when the path is missing."""
    text = raw.strip().strip("'\"").strip()
    if not text:
        raise ValueError("usage: /url <base_url>")
    if "://" not in text:
        text = "https://" + text
    text = text.rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"not a valid base URL: {text}")
    if parsed.path.rstrip("/") == "":
        text += "/v1"
    return text


def probe_base_url(base_url: str, api_key: str = "", timeout: float = 8):
    """GET ``{base_url}/models`` and return ``(ok, detail, models, kind)``."""
    import json
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    from .errors import classify_api_failure, explain_failure

    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if "api.anthropic.com" in base_url and api_key:
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    key_var = env_name_for_api_key(base_url)
    try:
        with urlopen(Request(url, headers=headers), timeout=timeout) as resp:
            payload = json.load(resp)
    except json.JSONDecodeError:
        return (
            False,
            f"Reached {url} but the models response was not JSON.",
            [],
            "server",
        )
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        kind = classify_api_failure(exc) or "connect"
        detail = explain_failure(
            kind, exc, base_url=base_url, key_var=key_var
        )
        return False, detail, [], kind
    except Exception as exc:
        kind = classify_api_failure(exc) or "connect"
        detail = explain_failure(
            kind, exc, base_url=base_url, key_var=key_var
        )
        return False, detail, [], kind
    names = names_from_model_payload(payload)
    shown = ", ".join(names[:8]) if names else "(none listed)"
    extra = f" (+{len(names) - 8} more)" if len(names) > 8 else ""
    detail = f"reachable {url} — {len(names)} models: {shown}{extra}"
    return True, detail, names, "ok"


def host_label(base_url: str) -> str:
    """Short name for the header: cloud, local, router, gpu, claude, or custom."""
    if "api.anthropic.com" in base_url:
        return "claude"
    if "11434" in base_url:
        return "local"
    if "ollama.com" in base_url:
        return "cloud"
    if "endpoints.huggingface.cloud" in base_url:
        return "gpu"
    if "huggingface.co" in base_url:
        return "router"
    return "custom"


def host_model_env(kind: str) -> str:
    if kind == "cloud":
        return "HFAGENT_CLOUD_MODEL"
    if kind == "local":
        return "HFAGENT_LOCAL_MODEL"
    return ""


def save_host_model(kind: str, model: str, path: Path | None = None) -> Path | None:
    """Remember the last model used on cloud or local."""
    name = host_model_env(kind)
    if not name:
        return None
    return _set_env_line(name, model, path)


def env_name_for_api_key(base_url: str) -> str:
    """Which .env variable a pasted key should update for this host.

    When ``HFAGENT_API_KEY`` is set it is the key actually sent, so a paste
    updates that variable. Custom hosts (tunnels, a Kaggle Ollama) use
    ``HFAGENT_API_KEY`` rather than a stale ``HF_TOKEN``.
    """
    if (os.environ.get("HFAGENT_API_KEY") or "").strip():
        return "HFAGENT_API_KEY"
    if "api.anthropic.com" in base_url:
        return "ANTHROPIC_API_KEY"
    if "huggingface.co" in base_url or "huggingface.cloud" in base_url:
        return "HF_TOKEN"
    if "ollama.com" in base_url or "11434" in base_url:
        return "OLLAMA_API_KEY"
    return "HFAGENT_API_KEY"


def save_api_key(key: str, base_url: str, path: Path | None = None) -> Path:
    """Remember an API key in the project .env. Does not print the key."""
    return _set_env_line(env_name_for_api_key(base_url), key, path)


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
    if "api.anthropic.com" in host and api_key:
        # Anthropic's models endpoint authenticates with x-api-key, not Bearer.
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    for url in urls:
        try:
            with urlopen(Request(url, headers=headers), timeout=8) as resp:
                payload = json.load(resp)
        except Exception:
            continue
        names = names_from_model_payload(payload)
        if names:
            return names
    if "api.anthropic.com" in host:
        return list(ANTHROPIC_FALLBACK_MODELS)
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
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5-5"
# Offline fallback for the model picker; the live /models list is used when
# reachable, so new model IDs appear automatically via f2.
ANTHROPIC_FALLBACK_MODELS = [
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-haiku-4-5-20251001",
]
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
    context_tokens: int = DEFAULT_CONTEXT_TOKENS
    fallback_base_url: str = ""
    fallback_model: str = ""
    fallback_api_key: str = ""
    api_key_var: str = ""
    sources: dict[str, str] = field(default_factory=dict)

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
        sources: dict[str, str] = {}
        cli_model = model
        cli_base = base_url
        if ollama is not None:
            base_url = OLLAMA_BASE_URL
            requested = ollama or os.environ.get("HFAGENT_LOCAL_MODEL") or ""
            model = _resolve_ollama_name(requested) if requested else _first_ollama_model()
            sources["base_url"] = "command line (--ollama)"
            sources["model"] = "command line (--ollama)"
        elif local:
            base_url = base_url or LOCAL_BASE_URL
            model = os.path.expanduser(
                model or os.environ.get("HFAGENT_LOCAL_MODEL", DEFAULT_LOCAL_MODEL)
            )
            sources["base_url"] = "command line (--local)"
            sources["model"] = "command line (--local)"

        if "base_url" in sources:
            resolved_base = base_url or DEFAULT_BASE_URL
        elif cli_base is not None:
            resolved_base = cli_base
            sources["base_url"] = "command line"
        else:
            resolved_base, sources["base_url"] = _from_env("HF_BASE_URL", DEFAULT_BASE_URL)

        if "model" not in sources:
            if cli_model is not None:
                model = cli_model
                sources["model"] = "command line"
        resolved_model = model or os.environ.get("HFAGENT_MODEL") or DEFAULT_MODEL
        if "model" not in sources:
            if model:
                sources["model"] = source_of("HFAGENT_MODEL") if os.environ.get("HFAGENT_MODEL") == model else "command line"
            elif os.environ.get("HFAGENT_MODEL"):
                sources["model"] = source_of("HFAGENT_MODEL")
            else:
                sources["model"] = "default"

        key, key_var = api_key_info(resolved_base)
        sources["api_key"] = source_of(key_var) if key else "default"
        sources["api_key_var"] = sources["api_key"]

        steps, sources["max_steps"] = _from_env_int("HFAGENT_MAX_STEPS", 25, max_steps)
        context_tokens, sources["context_tokens"] = _from_env_int(
            "HFAGENT_CONTEXT_TOKENS", DEFAULT_CONTEXT_TOKENS, None
        )
        if temperature is not None:
            resolved_temp = float(temperature)
            sources["temperature"] = "command line"
        else:
            resolved_temp = 0.0
            sources["temperature"] = "default"

        if approval is not None:
            resolved_approval = approval
            sources["approval"] = "command line"
        elif os.environ.get("HFAGENT_APPROVAL"):
            resolved_approval = os.environ["HFAGENT_APPROVAL"]
            sources["approval"] = source_of("HFAGENT_APPROVAL")
        elif os.environ.get("HFAGENT_YOLO") == "1":
            resolved_approval = "auto"
            sources["approval"] = source_of("HFAGENT_YOLO")
        else:
            resolved_approval = "default"
            sources["approval"] = "default"

        fallback_base, sources["fallback_base_url"] = _from_env(
            "HFAGENT_FALLBACK_BASE_URL", ""
        )
        fallback_model, sources["fallback_model"] = _from_env(
            "HFAGENT_FALLBACK_MODEL", ""
        )
        if os.environ.get("HFAGENT_FALLBACK_API_KEY"):
            fallback_key = os.environ["HFAGENT_FALLBACK_API_KEY"].strip()
            sources["fallback_api_key"] = source_of("HFAGENT_FALLBACK_API_KEY")
        else:
            fallback_key = ""
            sources["fallback_api_key"] = "default"

        return cls(
            api_key=key,
            api_key_var=key_var,
            base_url=resolved_base,
            model=resolved_model,
            max_steps=steps,
            approval=resolved_approval,
            temperature=resolved_temp,
            context_tokens=context_tokens,
            fallback_base_url=fallback_base,
            fallback_model=fallback_model,
            fallback_api_key=fallback_key,
            sources=sources,
        )


def mask_secret(value: str) -> str:
    """Keep a secret recognizable without printing it."""
    if not value:
        return "(unset)"
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}…{value[-4:]}"


def format_config_report(config: "Config") -> str:
    """Effective settings and the source that won. Secrets are masked."""
    rows: list[tuple[str, str, bool]] = [
        ("base_url", config.base_url, False),
        ("model", config.model, False),
        ("api_key", config.api_key, True),
        ("api_key_var", config.api_key_var or "(unset)", False),
        ("context_tokens", str(config.context_tokens), False),
        ("max_steps", str(config.max_steps), False),
        ("approval", config.approval, False),
        ("temperature", str(config.temperature), False),
        ("fallback_base_url", config.fallback_base_url or "(unset)", False),
        ("fallback_model", config.fallback_model or "(unset)", False),
        ("fallback_api_key", config.fallback_api_key, True),
    ]
    lines = [
        "Effective settings. Highest source wins:",
        "  command line > process environment > project .env > "
        "~/.hfagent/.env > ~/.hfagent/hf.env > default",
        "A process value copied from sourcing ~/.hfagent/hf.env is treated as "
        "that file, so it does not hide a newer project or user env file.",
        "",
    ]
    for name, value, secret in rows:
        shown = mask_secret(value) if secret else (value or "(unset)")
        source = config.sources.get(name, "default")
        lines.append(f"{name}={shown}  [{source}]")
    lines.extend(_browser_report_lines())
    lines.extend(_iheye_report_lines())
    return "\n".join(lines)


def _iheye_report_lines() -> list[str]:
    try:
        from .iheye import config_report_lines
    except Exception:
        return []
    return config_report_lines()


def _browser_report_lines() -> list[str]:
    from .browser import BrowserSettings, playwright_available

    settings = BrowserSettings.from_env()

    def src(name: str) -> str:
        return source_of(name) if os.environ.get(name) else "default"

    installed = "yes" if playwright_available() else "no"
    return [
        "",
        "Browser (dedicated Chrome profile, starts on first browser tool):",
        f"browser_headless={str(settings.headless).lower()}  [{src('HFAGENT_BROWSER_HEADLESS')}]",
        f"browser_profile={settings.profile}  [{src('HFAGENT_BROWSER_PROFILE')}]",
        f"browser_download_dir={settings.download_dir}  [{src('HFAGENT_BROWSER_DOWNLOAD_DIR')}]",
        f"browser_channel={settings.channel or '(auto)'}  [{src('HFAGENT_BROWSER_CHANNEL')}]",
        f"browser_executable={settings.executable or '(auto)'}  [{src('HFAGENT_BROWSER_EXECUTABLE')}]",
        f"browser_timeout_ms={settings.timeout_ms}  [{src('HFAGENT_BROWSER_TIMEOUT_MS')}]",
        f"browser_max_chars={settings.max_chars}  [{src('HFAGENT_BROWSER_MAX_CHARS')}]",
        f"playwright_installed={installed}",
    ]
