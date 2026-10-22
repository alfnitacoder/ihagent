"""Detect a test framework, run it, or fall back to a smoke check."""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
from pathlib import Path

_SKIP = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    "dist", "build", ".mypy_cache", ".pytest_cache",
}
_PLACEHOLDER = re.compile(r"no test specified", re.IGNORECASE)
_LOCAL_PORTS = (3000, 5173, 8080, 8000)


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _python_project(root: Path) -> bool:
    if any((root / name).is_file() for name in (
        "pyproject.toml", "requirements.txt", "pytest.ini", "setup.py", "setup.cfg",
    )):
        return True
    if any(root.glob("test_*.py")):
        return True
    tests = root / "tests"
    if tests.is_dir():
        for path in tests.rglob("*.py"):
            if not set(path.parts) & _SKIP:
                return True
    return False


def _has_python_tests(root: Path) -> bool:
    if any(root.glob("test_*.py")) or any(root.glob("tests/test_*.py")):
        return True
    for name in ("pytest.ini", "conftest.py"):
        if (root / name).is_file() or (root / "tests" / name).is_file():
            return True
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and "pytest" in pyproject.read_text(encoding="utf-8", errors="replace"):
        return True
    tests = root / "tests"
    return tests.is_dir() and any(tests.rglob("test_*.py"))


def detect_frameworks(root: Path) -> list[dict]:
    """Return at most a few commands. Each item has name, cmd, cwd."""
    root = Path(root).expanduser()
    found: list[dict] = []
    package = root / "package.json"
    if package.is_file():
        data = _load_json(package)
        scripts = data.get("scripts") or {}
        deps: dict = {}
        deps.update(data.get("dependencies") or {})
        deps.update(data.get("devDependencies") or {})
        test_script = str(scripts.get("test") or "")
        real_script = bool(test_script) and not _PLACEHOLDER.search(test_script)
        if "vitest" in deps or re.search(r"\bvitest\b", test_script):
            found.append(_spec(root, "vitest", ["npx", "vitest", "run"]))
        elif "jest" in deps or re.search(r"\bjest\b", test_script):
            found.append(_spec(root, "jest", ["npx", "jest", "--watchAll=false"]))
        elif "mocha" in deps or re.search(r"\bmocha\b", test_script):
            found.append(_spec(root, "mocha", ["npx", "mocha"]))
        elif real_script:
            found.append(_spec(root, "npm", ["npm", "test", "--silent"]))
    if _python_project(root) and _has_python_tests(root):
        found.append(_spec(root, "pytest", [sys.executable, "-m", "pytest", "-q"]))
    if (root / "go.mod").is_file():
        found.append(_spec(root, "go", ["go", "test", "./..."]))
    return found[:3]


def _spec(root: Path, name: str, cmd: list[str]) -> dict:
    return {"name": name, "cmd": cmd, "cwd": str(root), "timeout": 180}


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _python_sources(root: Path, limit: int = 20) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*.py"):
        if set(path.parts) & _SKIP:
            continue
        found.append(path)
        if len(found) >= limit:
            break
    return found


def smoke_plan(root: Path) -> list[dict]:
    """py_compile, npm run build, and curl against a server that is already up."""
    root = Path(root).expanduser()
    plan: list[dict] = []
    sources = _python_sources(root)
    if sources:
        plan.append(_spec(
            root,
            "py_compile",
            [sys.executable, "-m", "py_compile", *[str(path) for path in sources]],
        ))
    package = root / "package.json"
    if package.is_file():
        scripts = (_load_json(package).get("scripts") or {})
        if scripts.get("build"):
            plan.append(_spec(root, "npm-build", ["npm", "run", "build"]))
    for port in _LOCAL_PORTS:
        if _port_open(port):
            plan.append(_spec(
                root,
                f"curl-{port}",
                [
                    "curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
                    f"http://127.0.0.1:{port}/",
                ],
            ))
    return plan


def scaffold_offer(root: Path) -> str:
    root = Path(root)
    if _python_project(root) or any(_python_sources(root, 1)):
        sample = "tests/test_smoke.py (compile the project's Python files)"
    elif (root / "package.json").is_file():
        sample = "tests/smoke.test.mjs (node --test)"
    else:
        sample = "tests/test_smoke.py or tests/smoke.test.mjs"
    return (
        "No test framework ran. A minimal smoke test can be added: "
        f"{sample}. Call run_tests with scaffold=true, or /test scaffold, "
        "to write it. Existing test files are left alone."
    )


_PY_SCAFFOLD = '''\
"""Smoke test: project Python files compile."""
import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".venv", "venv", "node_modules", ".git", "__pycache__", "dist", "build"}


def test_sources_compile():
    files = [
        path
        for path in ROOT.rglob("*.py")
        if not (set(path.parts) & SKIP) and path.name != "test_smoke.py"
    ]
    assert files, "no Python sources found"
    for path in files:
        py_compile.compile(str(path), doraise=True)
'''

_JS_SCAFFOLD = '''\
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("project root has files", () => {
  const names = fs.readdirSync(root);
  assert.ok(names.length > 0);
});
'''


def write_scaffold(root: Path) -> str:
    """Write one smoke test if that path is free. Never overwrites."""
    root = Path(root).expanduser()
    if _python_project(root) or any(_python_sources(root, 1)):
        target = root / "tests" / "test_smoke.py"
        body = _PY_SCAFFOLD
        how = f"{sys.executable} -m pytest {target}"
    else:
        target = root / "tests" / "smoke.test.mjs"
        body = _JS_SCAFFOLD
        how = f"node --test {target}"
    if target.exists():
        return f"scaffold already exists: {target}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return f"wrote smoke scaffold {target}\nrun it with: {how}"


def default_runner(spec: dict) -> tuple[int, str]:
    timeout = int(spec.get("timeout") or 180)
    try:
        proc = subprocess.run(
            spec["cmd"],
            cwd=spec["cwd"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except FileNotFoundError:
        return 127, f"command not found: {spec['cmd'][0]}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _tail(text: str, lines: int = 40) -> str:
    kept = text.splitlines()[-lines:]
    return "\n".join(kept) if kept else "(no output)"


def run_project_tests(
    path: str = ".",
    *,
    scaffold: bool = False,
    runner=None,
) -> str:
    """Detect, run, and fall back to a smoke check. ``runner`` is for tests."""
    root = Path(path or ".").expanduser()
    if root.is_file():
        root = root.parent
    if not root.exists():
        return f"Error: not a directory: {root}"
    run = runner or default_runner
    lines = [f"Project: {root}"]
    if scaffold:
        lines.append(write_scaffold(root))
    frameworks = detect_frameworks(root)
    if frameworks:
        lines.append("Detected: " + ", ".join(item["name"] for item in frameworks))
        for item in frameworks:
            code, output = run(item)
            shown = " ".join(item["cmd"])
            lines.append(f"--- {item['name']}: {shown} (exit {code}) ---")
            lines.append(_tail(output))
        return "\n".join(lines)
    lines.append(
        "No test framework detected (pytest, jest, mocha, vitest, npm test, go test)."
    )
    smokes = smoke_plan(root)
    if not smokes:
        lines.append("Smoke check: no Python files, no build script, no local server.")
    else:
        lines.append("Smoke check:")
        for item in smokes:
            code, output = run(item)
            lines.append(f"--- {item['name']} (exit {code}) ---")
            lines.append(_tail(output, 20))
    lines.append(scaffold_offer(root))
    return "\n".join(lines)


def parse_test_command(arg: str) -> tuple[str, bool]:
    """``/test``, ``/test scaffold``, ``/test path``, ``/test scaffold path``."""
    scaffold = False
    path = "."
    for part in arg.split():
        if part in {"scaffold", "--scaffold"}:
            scaffold = True
        else:
            path = part
    return path, scaffold
