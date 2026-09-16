"""Shell execution tool with an approval gate."""

from __future__ import annotations

import subprocess

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
        },
        "required": ["command"],
    }
    needs_approval = True

    def preview(self, command: str, timeout: int = 60) -> str:
        return f"$ {command}\n(timeout: {timeout}s)"

    def run(self, command: str, timeout: int = 60) -> str:
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
