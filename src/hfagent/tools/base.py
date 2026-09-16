"""Tool base class and registry."""

from __future__ import annotations

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

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        return [tool.spec() for tool in self._tools.values()]
