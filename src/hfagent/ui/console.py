"""Rich-based console UI implementing the AgentUI interface."""

from __future__ import annotations

import json

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ..agent import AgentUI
from .. import __version__

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
            Text("hfagent", style="bold cyan"),
            f"v{__version__} — {model}",
            style="dim",
        )
        c.print(f"tools: {', '.join(tools)}", style="dim")
        c.print("type /help for commands, /exit to quit", style="dim")
        c.print()

    def on_status(self, text: str) -> None:
        self.console.print(text, style="dim")

    def on_assistant_delta(self, text: str) -> None:
        self.console.print(text, end="", markup=False, highlight=False, soft_wrap=True)

    def on_assistant_done(self, text: str) -> None:
        self.console.print()

    def on_action(self, tool_name: str, arguments: dict) -> None:
        self.console.print(
            Panel(
                _dump(arguments),
                title=f"action: {tool_name}",
                title_align="left",
                border_style="magenta",
            )
        )

    def on_observation(self, tool_name: str, result: str) -> None:
        snippet = result if len(result) <= MAX_OBS_CHARS else result[:MAX_OBS_CHARS] + " …"
        self.console.print(
            Panel(
                snippet,
                title=f"observation: {tool_name}",
                title_align="left",
                border_style="dim",
            )
        )

    def on_error(self, message: str) -> None:
        self.console.print(f"error: {message}", style="bold red")

    def approve(self, tool_name: str, arguments: dict) -> bool:
        self.console.print(
            Panel(
                _dump(arguments),
                title=f"approval needed: {tool_name}",
                title_align="left",
                border_style="yellow",
            )
        )
        try:
            answer = input("run this? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        return answer in ("y", "yes")
