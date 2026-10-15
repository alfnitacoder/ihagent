"""Tool base class and registry."""

from __future__ import annotations

import threading
from typing import Any


class Tool:
    """A callable tool exposed to the model via function calling."""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}
    needs_approval: bool = False

    def run(self, **kwargs: Any) -> str:
        raise NotImplementedError

    def preview(self, **kwargs: Any) -> str | None:
        """Human-readable preview shown at approval time; None -> show args."""
        return None

    def requires_approval(self, **kwargs: Any) -> bool:
        """Whether this call should go through the approval UI.

        Defaults to ``needs_approval``. Tools that are only sometimes
        risky (form submit, purchase, executable download) override this.
        """
        return self.needs_approval

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._lock = threading.Lock()

    def register(self, tool: Tool) -> None:
        with self._lock:
            if tool.name in self._tools:
                raise ValueError(f"duplicate tool name: {tool.name}")
            self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        with self._lock:
            self._tools.pop(name, None)

    def swap(self, previous: list[str], tools: list[Tool]) -> None:
        """Replace ``previous`` names with ``tools`` as one update."""
        with self._lock:
            owned = set(previous)
            for tool in tools:
                if tool.name in self._tools and tool.name not in owned:
                    raise ValueError(f"duplicate tool name: {tool.name}")
            for name in previous:
                self._tools.pop(name, None)
            for tool in tools:
                self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        with self._lock:
            return self._tools.get(name)

    def names(self) -> list[str]:
        with self._lock:
            return list(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        with self._lock:
            tools = list(self._tools.values())
        return [tool.spec() for tool in tools]
