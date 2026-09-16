"""Remote execution tool: run single commands over SSH (non-interactive)."""

from __future__ import annotations

import shlex
import subprocess
from datetime import datetime

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
                "description": "Seconds before a FOREGROUND command is killed. Default 60.",
            },
            "background": {
                "type": "boolean",
                "description": "Run detached with nohup so the task survives "
                "disconnection; returns a PID and log path to poll. Use for "
                "long tasks (scans, installs, builds).",
            },
        },
        "required": ["host", "command"],
    }
    needs_approval = True

    def preview(
        self, host: str, command: str, timeout: int = 60, background: bool = False
    ) -> str:
        if background:
            return f"$ ssh {host} 'nohup {command} > <log> 2>&1 &' (detached)"
        return f"$ ssh {host} '{command}'\n(timeout: {timeout}s)"

    @staticmethod
    def _argv(host: str, remote_command: str) -> list[str]:
        return [
            "ssh",
            "-o", "ConnectTimeout=10",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            # connection multiplexing: poll calls reuse one master connection,
            # so repeated checks are near-instant instead of re-handshaking
            "-o", "ControlMaster=auto",
            "-o", "ControlPath=~/.ssh/hfagent-cm-%C",
            "-o", "ControlPersist=600",
            host,
            remote_command,
        ]

    def _start_background(self, host: str, command: str) -> str:
        """Start the task detached and return immediately.

        The task runs as `sh -c` with ALL output redirected to the log, so
        nothing holds the ssh channel open (instant return), and a BG_DONE
        marker at the end gives reliable completion detection.
        """
        log = f"/tmp/hfagent-task-{datetime.now().strftime('%H%M%S')}.log"
        qlog = shlex.quote(log)
        # group the command so $? is its exit status; stream its output into
        # the log, then drop exit-status and done markers for poll detection
        inner = (
            f"( {command} ) >> {qlog} 2>&1 ; "
            f"echo BG_EXIT:$? >> {qlog} ; "
            f"echo BG_DONE >> {qlog}"
        )
        remote = (
            f"nohup sh -c {shlex.quote(inner)} "
            f"> /dev/null 2>&1 < /dev/null & echo BG_PID:$!"
        )
        try:
            proc = subprocess.run(
                self._argv(host, remote),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return "Error: could not start background task (ssh took >30s)"
        pid = ""
        for line in (proc.stdout or "").splitlines():
            if line.strip().startswith("BG_PID:"):
                pid = line.split("BG_PID:", 1)[1].strip()
        lines = [f"started in background on {host} (exit code: {proc.returncode})"]
        lines.append(f"pid: {pid or '(unknown)'}")
        lines.append(f"log: {log}")
        lines.append(
            f"check progress: ssh_run(host='{host}', "
            f"command='tail -n 30 {log}')"
        )
        lines.append(
            f"check finished + exit status: ssh_run(host='{host}', "
            f"command='grep BG_DONE {log} > /dev/null && grep BG_EXIT {log} "
            f"|| echo RUNNING')"
        )
        return "\n".join(lines)

    def run(
        self, host: str, command: str, timeout: int = 60, background: bool = False
    ) -> str:
        if background:
            return self._start_background(host, command)
        argv = self._argv(host, command)
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
