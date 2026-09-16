"""Remote execution tool: run single commands over SSH (non-interactive)."""

from __future__ import annotations

import subprocess

from .base import Tool

MAX_OUTPUT_CHARS = 20_000


class SshRun(Tool):
    name = "ssh_run"
    description = (
        "Run ONE command on a remote SSH host and return its exit code plus "
        "output. Hosts are aliases from ~/.ssh/config (e.g. 'wantok20'). "
        "Non-interactive: never opens a shell on the remote host. For several "
        "steps, call this tool once per command. Requires user approval."
    )
    parameters = {
        "type": "object",
        "properties": {
            "host": {
                "type": "string",
                "description": "Host alias from ~/.ssh/config.",
            },
            "command": {
                "type": "string",
                "description": "Shell command to execute on the remote host.",
            },
            "timeout": {
                "type": "integer",
                "description": "Seconds before the command is killed. Default 60.",
            },
        },
        "required": ["host", "command"],
    }
    needs_approval = True

    def preview(self, host: str, command: str, timeout: int = 60) -> str:
        return f"$ ssh {host} '{command}'\n(timeout: {timeout}s)"

    def run(self, host: str, command: str, timeout: int = 60) -> str:
        argv = [
            "ssh",
            "-o", "ConnectTimeout=10",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            host,
            command,
        ]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout}s"
        except FileNotFoundError:
            return "Error: ssh client not found"
        out = (proc.stdout or "") + (proc.stderr or "")
        if len(out) > MAX_OUTPUT_CHARS:
            out = out[:MAX_OUTPUT_CHARS] + "\n... [truncated]"
        header = f"exit code: {proc.returncode}"
        return f"{header}\n{out}" if out else header
