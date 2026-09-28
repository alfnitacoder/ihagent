"""Shell execution tool with an approval gate."""

from __future__ import annotations

import re
import shlex
import socket
import subprocess
from datetime import datetime

from .base import Tool

MAX_OUTPUT_CHARS = 20_000

SERVER_START_RE = re.compile(
    r"(?:^|[\s;&|])(?:npm\s+(?:run\s+)?(?:start|dev)|npx\s+.*(?:vite|serve)|"
    r"webpack(?:-dev-server)?(?:\s+serve)?|vite(?:\s|$)|"
    r"python(?:3)?\s+-m\s+http\.server)",
    re.IGNORECASE,
)
PORT_FLAG_RE = re.compile(r"(?:--port|-p|--listen)\s+(\d+)", re.IGNORECASE)


def _port_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def _guess_server_port(command: str) -> int | None:
    match = PORT_FLAG_RE.search(command)
    if match:
        return int(match.group(1))
    lower = command.lower()
    if "vite" in lower:
        return 5173
    if "webpack" in lower or re.search(r"npm\s+(?:run\s+)?(?:start|dev)", lower):
        return 3000
    if "http.server" in lower:
        return 8000
    return None


def _already_running_message(port: int, command: str) -> str:
    return (
        f"SKIPPED start: port {port} is already accepting connections "
        f"(something is already serving the web app).\n"
        f"Do NOT start another server on a different port.\n"
        f"Verify with: curl -s -o /dev/null -w '%{{http_code}}' "
        f"http://127.0.0.1:{port}/\n"
        f"Or open http://127.0.0.1:{port}/ — the requested command was: {command}"
    )


class RunCommand(Tool):
    name = "run_command"
    description = (
        "Run a shell command; returns exit code and output. Requires user "
        "approval. Prefer quiet flags for noisy tools (e.g. curl -s, wget -q). "
        "Never open interactive sessions (bare ssh, vim) - they hang; "
        "use ssh_run for remote hosts instead. For web servers use "
        "background=true once; first check if the port is already up "
        "(curl http://127.0.0.1:PORT) and reuse it — do not start a second "
        "server on another port."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer"},
            "background": {
                "type": "boolean",
                "description": "Run detached with nohup; returns a PID and "
                "log path to poll. Use for long tasks (builds, scans, "
                "dev servers).",
            },
        },
        "required": ["command"],
    }
    needs_approval = True

    def preview(
        self, command: str, timeout: int = 60, background: bool = False
    ) -> str:
        if background:
            return f"$ nohup {command} > <log> 2>&1 & (detached)"
        return f"$ {command}\n(timeout: {timeout}s)"

    def run(
        self, command: str, timeout: int = 60, background: bool = False
    ) -> str:
        if SERVER_START_RE.search(command):
            port = _guess_server_port(command)
            if port is not None and _port_listening(port):
                return _already_running_message(port, command)

        if background:
            log = f"/tmp/hfagent-task-{datetime.now().strftime('%H%M%S')}.log"
            # Wrap in bash -c so `cd ... && cmd` keeps the new cwd. Plain
            # `nohup cd ... && npm` only nohups `cd`, then npm runs in the
            # original directory (breaks monorepo sub-apps like taskwebapp).
            wrapped = (
                f"nohup bash -c {shlex.quote(command)} > {log} 2>&1 < /dev/null "
                f"& echo BG_PID:$!"
            )
            proc = subprocess.run(
                wrapped, shell=True, capture_output=True, text=True, timeout=15
            )
            pid = ""
            for line in (proc.stdout or "").splitlines():
                if line.strip().startswith("BG_PID:"):
                    pid = line.split("BG_PID:", 1)[1].strip()
            lines = [
                f"started in background (exit code: {proc.returncode})",
                f"pid: {pid or '(unknown)'}",
                f"log: {log}",
            ]
            if pid:
                lines.append(
                    f"check progress: run_command(command='tail -n 30 {log}')"
                )
                lines.append(
                    f"check finished: run_command(command="
                    f"'ps -p {pid} > /dev/null 2>&1 && echo RUNNING || echo FINISHED')"
                )
            port = _guess_server_port(command)
            if port is not None:
                lines.append(
                    f"If the app is up, use http://127.0.0.1:{port}/ — "
                    "do not start another server on a different port."
                )
            return "\n".join(lines)
        # foreground path (unchanged)
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout}s"
        out = (proc.stdout or "") + (proc.stderr or "")
        if "EADDRINUSE" in out or "address already in use" in out.lower():
            port = _guess_server_port(command)
            hint = (
                "\nHINT: port already in use"
                + (f" (likely {port})" if port else "")
                + ". Reuse the existing server — do not pick a new port."
            )
            out = out + hint
        if len(out) > MAX_OUTPUT_CHARS:
            out = out[:MAX_OUTPUT_CHARS] + "\n... [truncated]"
        header = f"exit code: {proc.returncode}"
        return f"{header}\n{out}" if out else header
