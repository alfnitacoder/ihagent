"""Shell execution tool with an approval gate."""

from __future__ import annotations

import subprocess
from datetime import datetime

from .base import Tool

MAX_OUTPUT_CHARS = 20_000


class RunCommand(Tool):
    name = "run_command"
    description = (
        "Run a shell command; returns exit code and output. Requires user "
        "approval. Prefer quiet flags for noisy tools (e.g. curl -s, wget -q). "
        "Never open interactive sessions (bare ssh, vim) - they hang; "
        "use ssh_run for remote hosts instead."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer"},
            "background": {
                "type": "boolean",
                "description": "Run detached with nohup; returns a PID and "
                "log path to poll. Use for long tasks (builds, scans).",
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
        if background:
            log = f"/tmp/hfagent-task-{datetime.now().strftime('%H%M%S')}.log"
            wrapped = f"nohup {command} > {log} 2>&1 < /dev/null & echo BG_PID:$!"
            proc = subprocess.run(
                wrapped, shell=True, capture_output=True, text=True, timeout=15
            )
            pid = ""
            for line in (proc.stdout or "").splitlines():
                if line.strip().startswith("BG_PID:"):
                    pid = line.split("BG_PID:", 1)[1].strip()
            lines = [f"started in background (exit code: {proc.returncode})",
                     f"pid: {pid or '(unknown)'}", f"log: {log}"]
            if pid:
                lines.append(f"check progress: run_command(command='tail -n 30 {log}')")
                lines.append(
                    f"check finished: run_command(command='ps -p {pid} > /dev/null 2>&1 && echo RUNNING || echo FINISHED')"
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
        if len(out) > MAX_OUTPUT_CHARS:
            out = out[:MAX_OUTPUT_CHARS] + "\n... [truncated]"
        header = f"exit code: {proc.returncode}"
        return f"{header}\n{out}" if out else header
