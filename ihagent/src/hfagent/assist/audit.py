"""Read-only repository health check for ``ihagent --audit`` and ``/audit``."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _license_field(root: Path) -> str:
    package = _load_json(root / "package.json")
    license_value = package.get("license")
    if isinstance(license_value, str) and license_value.strip():
        return f"package.json license={license_value.strip()}"
    text_path = root / "pyproject.toml"
    if text_path.is_file():
        try:
            text = text_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("license") and "=" in stripped:
                return f"pyproject.toml {stripped}"
    return ""


def _test_signals(root: Path) -> list[str]:
    found: list[str] = []
    package = _load_json(root / "package.json")
    script = str((package.get("scripts") or {}).get("test") or "")
    if script and "no test specified" not in script.lower():
        found.append("package.json scripts.test")
    for name in ("pytest.ini", "tox.ini"):
        if (root / name).is_file():
            found.append(name)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and "pytest" in pyproject.read_text(encoding="utf-8", errors="replace").lower():
        found.append("pyproject pytest")
    if (root / "tests").is_dir() or any(root.glob("test_*.py")):
        found.append("tests/ or test_*.py")
    if any(root.glob("**/*.test.js")) or any(root.glob("**/*.test.ts")):
        found.append("*.test.js/ts")
    return found


def _lint_signals(root: Path) -> list[str]:
    found: list[str] = []
    package = _load_json(root / "package.json")
    scripts = package.get("scripts") or {}
    if scripts.get("lint"):
        found.append("package.json scripts.lint")
    names = (
        ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json",
        "eslint.config.js", "eslint.config.mjs", ".prettierrc",
        "prettier.config.js", "biome.json", "ruff.toml", ".flake8",
        ".pylintrc", "phpcs.xml",
    )
    for name in names:
        if (root / name).exists():
            found.append(name)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8", errors="replace").lower()
        if "[tool.ruff" in text or "[tool.flake8" in text:
            found.append("pyproject linter")
    return found


def _ci_files(root: Path) -> list[str]:
    found: list[str] = []
    workflows = root / ".github" / "workflows"
    if workflows.is_dir() and any(workflows.glob("*.yml")) or (
        workflows.is_dir() and any(workflows.glob("*.yaml"))
    ):
        found.append(".github/workflows")
    for name in (
        ".gitlab-ci.yml", "azure-pipelines.yml", "Jenkinsfile",
        ".circleci/config.yml", ".travis.yml", "bitbucket-pipelines.yml",
    ):
        if (root / name).exists():
            found.append(name)
    return found


def _dep_notes(root: Path) -> tuple[str, bool]:
    notes: list[str] = []
    warn = False
    requirements = root / "requirements.txt"
    if requirements.is_file():
        unpinned: list[str] = []
        try:
            lines = requirements.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            if "==" in stripped or "~=" in stripped or "@" in stripped:
                continue
            unpinned.append(stripped.split()[0][:40])
        if unpinned:
            warn = True
            shown = ", ".join(unpinned[:5])
            notes.append(f"{len(unpinned)} unpinned in requirements.txt ({shown})")
        else:
            notes.append("requirements.txt uses ==")
    package_path = root / "package.json"
    if package_path.is_file():
        data = _load_json(package_path)
        deps: dict = {}
        deps.update(data.get("dependencies") or {})
        deps.update(data.get("devDependencies") or {})
        loose = [
            name for name, spec in deps.items()
            if str(spec).strip() in {"*", "latest", ""}
        ]
        if loose:
            warn = True
            notes.append("loose versions: " + ", ".join(loose[:5]))
        lock = [
            name for name in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml")
            if (root / name).is_file()
        ]
        if deps and not lock:
            warn = True
            notes.append("no lockfile (npm, yarn, or pnpm)")
        elif lock:
            notes.append("lockfile " + lock[0])
        if _network_enabled():
            notes.append(_npm_outdated(root))
        elif deps:
            notes.append("registry outdated check skipped (HFAGENT_AUDIT_NETWORK=1)")
    if not notes:
        return "no package.json or requirements.txt", False
    return "; ".join(notes), warn


def _network_enabled() -> bool:
    return os.environ.get("HFAGENT_AUDIT_NETWORK", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _npm_outdated(root: Path) -> str:
    if not (root / "package.json").is_file():
        return "npm outdated skipped"
    try:
        proc = subprocess.run(
            ["npm", "outdated", "--json"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "npm outdated unavailable"
    raw = (proc.stdout or "").strip()
    if not raw:
        return "npm outdated: none reported" if proc.returncode == 0 else "npm outdated failed"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "npm outdated returned unreadable output"
    if not isinstance(data, dict) or not data:
        return "npm outdated: none reported"
    names = list(data)[:6]
    extra = f" (+{len(data) - len(names)} more)" if len(data) > len(names) else ""
    return f"npm outdated: {', '.join(names)}{extra}"


def audit(root: Path | str) -> tuple[str, int]:
    """Return ``(report, warning_count)``. Does not modify the tree."""
    path = Path(root).expanduser()
    if not path.exists():
        return f"audit: path not found: {path}", 1
    if path.is_file():
        path = path.parent
    path = path.resolve()
    checks: list[tuple[str, str, str]] = []

    def add(level: str, title: str, detail: str) -> None:
        checks.append((level, title, detail))

    try:
        names = [item.name for item in path.iterdir() if item.is_file()]
    except OSError as exc:
        return f"audit: cannot read {path}: {exc}", 1
    readmes = [name for name in names if name.lower().startswith("readme")]
    if readmes:
        add("ok", "README", readmes[0])
    else:
        add("warn", "README", "missing README.md (or README.rst / README.txt)")

    licenses = [name for name in names if name.lower().startswith(("license", "licence", "copying"))]
    field = _license_field(path)
    if licenses:
        add("ok", "license", licenses[0])
    elif field:
        add("ok", "license", field)
    else:
        add("warn", "license", "no LICENSE file and no license field in package.json or pyproject.toml")

    tests = _test_signals(path)
    if tests:
        add("ok", "tests", "; ".join(tests))
    else:
        add("warn", "tests", "no test script, pytest config, or tests directory")

    lint = _lint_signals(path)
    if lint:
        add("ok", "lint", "; ".join(lint))
    else:
        add("warn", "lint", "no lint script or linter config (eslint, ruff, flake8, prettier)")

    ci = _ci_files(path)
    if ci:
        add("ok", "CI", ", ".join(ci))
    else:
        add("warn", "CI", "no .github/workflows, .gitlab-ci.yml, Jenkinsfile, or similar")

    if (path / ".gitignore").is_file():
        add("ok", "gitignore", ".gitignore present")
    else:
        add("info", "gitignore", "no .gitignore")

    dep_text, dep_warn = _dep_notes(path)
    add("warn" if dep_warn else "ok", "dependencies", dep_text)

    try:
        from .security import scan_project

        findings = scan_project(path, limit=40)
    except Exception:
        findings = []
    if findings:
        kinds = sorted({item["kind"] for item in findings})
        add(
            "warn",
            "security",
            f"{len(findings)} finding(s): {', '.join(kinds)}. "
            "Run /security for the checklist. Nothing was changed.",
        )
    else:
        add("ok", "security", "no default credentials, debug flags, or obvious hardcoded secrets in a quick scan")

    warnings = sum(1 for level, _title, _detail in checks if level == "warn")
    lines = [f"IHAgent audit — {path}", ""]
    for level, title, detail in checks:
        lines.append(f"{level.upper():4}  {title}: {detail}")
    lines.append("")
    if warnings:
        lines.append(f"{warnings} warning(s). Suggested actions:")
        for level, title, detail in checks:
            if level == "warn":
                lines.append(f"  - {title}: {detail}")
    else:
        lines.append("No warnings.")
    lines.append("Read-only. Nothing was installed or modified.")
    return "\n".join(lines), warnings
