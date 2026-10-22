"""Checklist for insecure project patterns. Changes wait for approval."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .settings import security_check

_SKIP = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".mypy_cache", ".pytest_cache",
}
_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".htm", ".vue", ".php", ".rb", ".env"}
_DEFAULT = re.compile(
    r"""(?i)\b(password|passwd|pwd)\b\s*[:=]\s*['\"]"""
    r"(admin|password|pass|123456|root|toor|changeme|default|guest|letmein)['\"]"
)
_DEBUG = re.compile(
    r"(?i)\b(debug|flask_debug)\b\s*[:=]\s*True\b|debug\s*=\s*True\b"
)
_AWS = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_SK = re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")
_PRIVATE = re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")
_ASSIGNED = re.compile(
    r"""(?i)\b(api_key|apikey|secret_key|aws_secret|private_key|access_token|auth_token)\b"""
    r"""\s*[:=]\s*['\"][^'\"]{8,}['\"]"""
)
_SKIP_LINE = re.compile(
    r"(?i)os\.environ|process\.env|getenv|your[-_]|placeholder|example|xxxx|todo|changeme_me",
)
_FORM = re.compile(r"<form\b[^>]*method\s*=\s*['\"]post['\"]", re.IGNORECASE)


def _redact(line: str) -> str:
    hidden = re.sub(r"(['\"])[^'\"]{4,}(['\"])", r"\1…\2", line.strip())
    return hidden[:180]


def _skip_line(line: str) -> bool:
    return bool(_SKIP_LINE.search(line))


def scan_text(path: str, text: str) -> list[dict]:
    """Findings in one file. Values are redacted."""
    findings: list[dict] = []
    suffix = Path(path).suffix.lower()
    for index, line in enumerate(text.splitlines(), 1):
        if _skip_line(line):
            continue
        if _DEFAULT.search(line):
            findings.append(_finding(
                path, "default_creds", index, line,
                "Replace the literal with an environment variable and refuse to start when it is unset.",
                "env",
            ))
        elif _ASSIGNED.search(line) or _AWS.search(line) or _SK.search(line):
            findings.append(_finding(
                path, "secret", index, line,
                "Move the secret to the environment or a secret store. Do not commit it.",
                "env",
            ))
        if _DEBUG.search(line):
            findings.append(_finding(
                path, "debug", index, line,
                "Turn debug off for anything that is not a local development process.",
                "debug_off",
            ))
    if _PRIVATE.search(text):
        findings.append(_finding(
            path, "secret", 1, "-----BEGIN PRIVATE KEY-----",
            "Remove the private key from the repo and load it from outside the tree.",
            "manual",
        ))
    if suffix in {".html", ".htm", ".vue", ".php", ".js", ".jsx", ".tsx"} and _FORM.search(text):
        if not re.search(r"csrf", text, re.IGNORECASE):
            findings.append(_finding(
                path, "csrf", 1, "<form method=post>",
                "Add a CSRF token to POST forms and reject requests that do not include it.",
                "manual",
            ))
    return findings


def _finding(path: str, kind: str, line: int, source: str, mitigation: str, apply: str) -> dict:
    return {
        "path": str(path),
        "kind": kind,
        "line": line,
        "detail": _redact(source),
        "mitigation": mitigation,
        "apply": apply,
    }


def scan_project(root: Path | str, *, limit: int = 80) -> list[dict]:
    folder = Path(root).expanduser()
    if folder.is_file():
        try:
            text = folder.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return scan_text(str(folder), text)
    if not folder.is_dir():
        return []
    found: list[dict] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [name for name in dirnames if name not in _SKIP and not name.startswith(".")]
        for name in filenames:
            if seen >= limit or len(found) >= limit:
                return found[:limit]
            path = Path(dirpath) / name
            if path.suffix.lower() not in _EXT and path.name != ".env":
                continue
            try:
                if path.stat().st_size > 200_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            seen += 1
            found.extend(scan_text(str(path), text))
    return found[:limit]


def format_findings(findings: list[dict], *, heading: str = "Security checklist") -> str:
    if not findings:
        return f"{heading}: nothing obvious (default credentials, CSRF on POST forms, hardcoded secrets, debug on)."
    lines = [
        heading + " (nothing is changed until you approve /security apply):",
    ]
    for item in findings[:20]:
        lines.append(
            f"- [{item['kind']}] {item['path']}:{item['line']} {item['detail']}"
        )
        lines.append(f"  mitigation: {item['mitigation']}")
    if len(findings) > 20:
        lines.append(f"- ... {len(findings) - 20} more")
    lines.append("Opt out of automatic notices with HFAGENT_SECURITY_CHECK=0.")
    return "\n".join(lines)


def findings_note(path: str, content: str) -> str:
    if not security_check():
        return ""
    found = scan_text(path, content)
    if not found:
        return ""
    return format_findings(found, heading=f"Security checklist ({Path(path).name})")


def _proposed_line(path: str, line: str, apply: str) -> str | None:
    if apply == "debug_off":
        updated = re.sub(r"\bTrue\b", "False", line, count=1)
        return updated if updated != line else None
    if apply != "env":
        return None
    if path.endswith(".py"):
        updated = re.sub(r"""(['\"])[^'\"]*(['\"])""", 'os.environ["APP_SECRET"]', line, count=1)
    elif path.endswith((".js", ".jsx", ".ts", ".tsx")):
        updated = re.sub(r"""(['\"])[^'\"]*(['\"])""", "process.env.APP_SECRET", line, count=1)
    else:
        return None
    return updated if updated != line else None


def apply_finding(item: dict) -> str:
    """Apply one mechanical mitigation. CSRF and private keys stay manual."""
    if item.get("apply") == "manual":
        return f"not applied automatically: {item['path']} ({item['kind']})"
    path = Path(item["path"])
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"skipped {path}: {exc}"
    lines = text.splitlines(keepends=True)
    index = int(item.get("line") or 1) - 1
    if index < 0 or index >= len(lines):
        return f"skipped {path}: line moved"
    original = lines[index]
    proposed = _proposed_line(str(path), original, item.get("apply") or "")
    if not proposed:
        return f"skipped {path}: no safe edit for {item['kind']}"
    newline = "\n" if original.endswith("\n") else ""
    lines[index] = proposed.rstrip("\n") + newline
    body = "".join(lines)
    if path.suffix == ".py" and "import os" not in body and "os.environ" in body:
        body = "import os\n" + body
    path.write_text(body, encoding="utf-8")
    return f"updated {path}:{item['line']} ({item['kind']})"


def apply_with_approval(root: Path | str, approve) -> str:
    """Ask ``approve(tool, args, preview)`` before each change."""
    findings = scan_project(root)
    if not findings:
        return format_findings([])
    lines = [format_findings(findings), "", "Applying only the changes you approve:"]
    for item in findings[:20]:
        if item.get("apply") == "manual":
            lines.append(f"left for you: {item['path']} {item['kind']} — {item['mitigation']}")
            continue
        preview = f"{item['detail']}\n-> {item['mitigation']}"
        try:
            allowed = bool(approve(
                "security",
                {"path": item["path"], "line": item["line"], "kind": item["kind"]},
                preview,
            ))
        except Exception:
            allowed = False
        if not allowed:
            lines.append(f"declined: {item['path']}:{item['line']} {item['kind']}")
            continue
        lines.append(apply_finding(item))
    if len(findings) > 20:
        lines.append("stopped after 20 findings")
    return "\n".join(lines)
