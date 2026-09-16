"""Shell execution tool with an approval gate."""

from __future__ import annotations

import subprocess

from .base import Tool

MAX_OUTPUT_CHARS = 20_000


class RunCommand(Tool):
    name = "run_command"
    description = (
        "Run a shell command in the current working directory and return its "
        "exit code plus combined stdout/stderr. Use for builds, tests, git, "
        "package management, etc."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command."},
            "timeout": {
                "type": "integer",
                "description": "Seconds before the command is killed. Default 60.",
            },
        },
        "required": ["command"],
    }
    needs_approval = True

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
