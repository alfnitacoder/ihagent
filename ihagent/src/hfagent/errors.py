"""Classify API and tunnel failures so the UI can name the cause."""

from __future__ import annotations

import re
from typing import Iterable

_STATUS_RE = re.compile(r"\b(?:error code|http)\s*[:=]?\s*(\d{3})\b", re.IGNORECASE)

_CONNECTION_KINDS = frozenset(
    {"dns", "tls", "timeout", "tunnel", "connect", "gateway"}
)

_KIND_LABELS = {
    "dns": "DNS failure",
    "tls": "TLS failure",
    "timeout": "timeout",
    "tunnel": "tunnel gone (Cloudflare 530 / connect error)",
    "connect": "tunnel gone (Cloudflare 530 / connect error)",
    "gateway": "gateway error",
    "auth": "401/403 auth",
    "not_found": "404 model not found",
    "server": "5xx",
}


def kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, kind or "connection failure")


def iter_exceptions(exc: BaseException) -> Iterable[BaseException]:
    seen: set[int] = set()
    stack: list[BaseException | None] = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        stack.append(current.__cause__)
        stack.append(current.__context__)
        reason = getattr(current, "reason", None)
        if isinstance(reason, BaseException):
            stack.append(reason)


def status_code_of(exc: BaseException) -> int | None:
    for item in iter_exceptions(exc):
        for attr in ("status_code", "code"):
            value = getattr(item, attr, None)
            if isinstance(value, int) and 100 <= value <= 599:
                return value
        match = _STATUS_RE.search(str(item))
        if match:
            return int(match.group(1))
    return None


def _blob(exc: BaseException) -> str:
    parts: list[str] = []
    for item in iter_exceptions(exc):
        parts.append(type(item).__name__)
        for base in type(item).__mro__:
            parts.append(base.__name__)
        parts.append(str(item))
    return " ".join(parts).lower()


def classify_api_failure(exc: BaseException) -> str | None:
    """Return a cause class, or None when this is not a transport/HTTP failure.

    Classes: dns, tls, timeout, tunnel, connect, gateway, auth, not_found, server.
    """
    status = status_code_of(exc)
    text = _blob(exc)
    if status in (401, 403) or "authenticationerror" in text or "permissiondenied" in text:
        return "auth"
    if status == 404 or "notfounderror" in text:
        return "not_found"
    if status == 530 or "error code: 1033" in text or ("cloudflare" in text and "530" in text):
        return "tunnel"
    if status in (502, 503, 504):
        return "gateway"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if any(token in text for token in ("certificate", "ssl", "tls", "handshake")):
        return "tls"
    if any(
        token in text
        for token in (
            "name or service not known",
            "nodename nor servname",
            "getaddrinfo",
            "name resolution",
            "temporary failure in name resolution",
            "gaierror",
        )
    ):
        return "dns"
    if status is not None and status >= 500:
        return "server"
    if status is not None and status < 500:
        return None
    if any(
        token in text
        for token in (
            "connection error",
            "connecterror",
            "apiconnectionerror",
            "connection refused",
            "connection reset",
            "failed to establish",
            "network is unreachable",
            "errno 111",
            "winerror 10061",
        )
    ):
        return "connect"
    return None


def is_connection_failure(exc: BaseException) -> bool:
    """True for unreachable hosts. Auth and other HTTP client errors are false."""
    return classify_api_failure(exc) in _CONNECTION_KINDS


def is_context_length_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "context length",
            "context_length",
            "maximum context",
            "context window",
            "too many tokens",
            "token limit",
            "reduce the length",
            "prompt is too long",
        )
    )


def explain_failure(
    kind: str,
    exc: BaseException,
    *,
    base_url: str = "",
    model: str = "",
    key_var: str = "",
) -> str:
    """One line naming the URL and the cause class."""
    url = base_url.strip() or "the API endpoint"
    key_var = key_var.strip() or "HFAGENT_API_KEY"
    model_name = model.strip() or "the requested model"
    status = status_code_of(exc)
    label = kind_label(kind)
    if kind == "dns":
        return f"Cannot reach {url}: {label}. The host name does not resolve."
    if kind == "tls":
        return f"Cannot reach {url}: {label}. The certificate or handshake was rejected."
    if kind == "timeout":
        return f"Cannot reach {url}: {label}. The host did not respond in time."
    if kind in ("tunnel", "connect"):
        return (
            f"Cannot reach {url}: {label}. "
            "The quick tunnel is down or the origin is offline."
        )
    if kind == "gateway":
        code = status or "5xx"
        return (
            f"Cannot reach {url}: {label} HTTP {code}. "
            "The tunnel or proxy is not passing traffic."
        )
    if kind == "auth":
        code = status if status in (401, 403) else "401/403"
        return (
            f"Authentication failed for {url}: {label} (HTTP {code}). "
            f"Set {key_var} to a valid key for this host."
        )
    if kind == "not_found":
        return (
            f"Model not found at {url}: {label} ('{model_name}'). "
            "Check the model id with /models."
        )
    if kind == "server":
        code = status or "5xx"
        return f"Server error from {url}: {label} (HTTP {code})."
    return str(exc).strip() or type(exc).__name__
