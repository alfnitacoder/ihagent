"""Rich-based console UI implementing the AgentUI interface."""

from __future__ import annotations

import json
import queue
import select
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ..agent import AgentUI
from .. import PRODUCT, __version__

MAX_OBS_CHARS = 600


def _dump(arguments: dict) -> str:
    try:
        return json.dumps(arguments, ensure_ascii=False, indent=2)
    except TypeError:
        return str(arguments)


class ConsoleUI(AgentUI):
    def __init__(self) -> None:
        self.console = Console()

    def banner(self, model: str, tools: list[str]) -> None:
        c = self.console
        c.print(
            Text(PRODUCT, style="bold cyan"),
            f"v{__version__} — {model}",
            style="dim",
        )
        c.print(f"tools: {', '.join(tools)}", style="dim")
        c.print("device: …    tokens: 0 in / 0 out", style="dim")
        c.print("type /help for commands, /exit to quit", style="dim")
        c.print()

    def on_runtime(self, device: str, prompt_tokens: int, completion_tokens: int) -> None:
        self.console.print(
            f"{device}    tokens: {prompt_tokens} in / {completion_tokens} out",
            style="dim",
            markup=False,
            highlight=False,
        )

    def on_status(self, text: str) -> None:
        # markup=False: dynamic text may contain [brackets] (sed, tags...)
        self.console.print(text, style="dim", markup=False, highlight=False)

    def on_thought_delta(self, text: str) -> None:
        self.console.print(
            text, end="", style="dim italic", markup=False, highlight=False, soft_wrap=True
        )

    def on_thought_done(self, text: str) -> None:
        if text:
            self.console.print()

    def on_assistant_delta(self, text: str) -> None:
        self.console.print(text, end="", markup=False, highlight=False, soft_wrap=True)

    def on_assistant_done(self, text: str) -> None:
        self.console.print()

    def on_action(self, tool_name: str, arguments: dict) -> None:
        self.console.print(
            Panel(
                Text(_dump(arguments)),
                title=f"action: {tool_name}",
                title_align="left",
                border_style="magenta",
            )
        )

    def on_observation(self, tool_name: str, result: str) -> None:
        snippet = result if len(result) <= MAX_OBS_CHARS else result[:MAX_OBS_CHARS] + " …"
        self.console.print(
            Panel(
                Text(snippet),
                title=f"observation: {tool_name}",
                title_align="left",
                border_style="dim",
            )
        )

    def on_system(self, text: str) -> None:
        self.console.print(text, markup=False, highlight=False)

    def on_error(self, message: str) -> None:
        # Never let error printing itself crash the REPL: render as literal
        # Text (no markup) and fall back to builtin print on any failure.
        try:
            self.console.print(
                Text(f"error: {message}", style="bold red")
            )
        except Exception:
            print(f"error: {message}")

    def approve(self, tool_name: str, arguments: dict, preview: str | None = None) -> bool:
        body = preview if preview is not None else _dump(arguments)
        self.console.print(
            Panel(
                Text(body),
                title=f"approval needed: {tool_name}",
                title_align="left",
                border_style="yellow",
            )
        )
        box = getattr(self, "eye_approvals", None)
        if isinstance(box, queue.Queue):
            answered = _approval_from_eye_or_stdin(box)
            if answered is not None:
                return answered
        try:
            answer = input("run this? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        return answer in ("y", "yes")


def _approval_from_eye_or_stdin(box: queue.Queue, timeout: float = 600.0) -> bool | None:
    """y/N from the keyboard or from the iheye card. None falls back to input()."""
    sys.stdout.write("run this? [y/N] ")
    sys.stdout.flush()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            yes = bool(box.get_nowait())
        except queue.Empty:
            yes = None
        if yes is not None:
            sys.stdout.write(("y" if yes else "n") + "\n")
            sys.stdout.flush()
            return yes
        try:
            ready, _, _ = select.select([sys.stdin], [], [], 0.15)
        except (OSError, ValueError):
            return None
        if not ready:
            continue
        try:
            line = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n")
            return False
        if line == "":
            return False
        return line.strip().lower() in ("y", "yes")
    return False
