"""Background installs after a manifest file changes."""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path

from .settings import auto_install


def plan_install(path: str) -> dict | None:
    """Pick npm, yarn, pnpm, or pip for a manifest. ``None`` if not a manifest."""
    target = Path(path).expanduser()
    name = target.name.lower()
    cwd = target.parent
    if name == "package.json":
        if (cwd / "pnpm-lock.yaml").exists():
            cmd, label = ["pnpm", "install"], "pnpm install"
        elif (cwd / "yarn.lock").exists():
            cmd, label = ["yarn", "install"], "yarn install"
        else:
            cmd, label = ["npm", "install"], "npm install"
        return {"cmd": cmd, "cwd": str(cwd), "label": label}
    if name == "requirements.txt" or (
        name.startswith("requirements") and name.endswith(".txt")
    ):
        return {
            "cmd": [sys.executable, "-m", "pip", "install", "-r", target.name],
            "cwd": str(cwd),
            "label": f"pip install -r {target.name}",
        }
    if name == "pyproject.toml":
        return {
            "cmd": [sys.executable, "-m", "pip", "install", "-e", "."],
            "cwd": str(cwd),
            "label": "pip install -e .",
        }
    if name == "pipfile":
        return {
            "cmd": ["pipenv", "install"],
            "cwd": str(cwd),
            "label": "pipenv install",
        }
    return None


def run_plan(plan: dict, *, timeout: int = 180) -> tuple[int, str]:
    """Run one install. Returns ``(exit_code, combined output)``."""
    try:
        proc = subprocess.run(
            plan["cmd"],
            cwd=plan["cwd"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        out = ""
        if isinstance(exc.stdout, str) or isinstance(exc.stderr, str):
            out = (exc.stdout or "") + (exc.stderr or "")
        return 124, out or f"timed out after {timeout}s"
    except FileNotFoundError:
        return 127, f"command not found: {plan['cmd'][0]}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _format(plan: dict, code: int, output: str) -> str:
    tail = "\n".join(output.splitlines()[-30:]) if output else ""
    if code == 0:
        return f"auto-install ok: {plan['label']} in {plan['cwd']}"
    detail = f"\n{tail}" if tail else ""
    return (
        f"auto-install failed: {plan['label']} in {plan['cwd']} "
        f"(exit {code}){detail}"
    )


class InstallScheduler:
    """Start installs off the agent thread and keep their results."""

    def __init__(self, runner=None, notify=None) -> None:
        self.runner = runner or run_plan
        self.notify = notify
        self.notes: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._inflight: set[tuple[str, str]] = set()
        self._threads: list[threading.Thread] = []

    def schedule(self, plan: dict) -> str:
        key = (plan["label"], plan["cwd"])
        with self._lock:
            if key in self._inflight:
                return f"auto-install already running: {plan['label']} ({plan['cwd']})"
            self._inflight.add(key)

        def work() -> None:
            try:
                code, output = self.runner(plan)
            except Exception as exc:
                code, output = 1, f"{type(exc).__name__}: {exc}"
            finally:
                with self._lock:
                    self._inflight.discard(key)
            message = _format(plan, code, output)
            self.notes.put(message)
            if self.notify is not None:
                try:
                    self.notify(message)
                except Exception:
                    pass

        thread = threading.Thread(target=work, name="hfagent-auto-install", daemon=True)
        with self._lock:
            self._threads.append(thread)
        thread.start()
        return (
            f"auto-install started in the background: {plan['label']} "
            f"({plan['cwd']}). Errors are reported when it finishes. "
            "Disable with HFAGENT_AUTO_INSTALL=0."
        )

    def drain(self) -> list[str]:
        found: list[str] = []
        while True:
            try:
                found.append(self.notes.get_nowait())
            except queue.Empty:
                return found

    def wait(self, timeout: float = 5) -> None:
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout)


def maybe_schedule(path: str, scheduler: InstallScheduler | None) -> str:
    """Return a status line, or "" when this path is not a manifest."""
    if not auto_install() or scheduler is None:
        return ""
    plan = plan_install(path)
    if plan is None:
        return ""
    return scheduler.schedule(plan)
