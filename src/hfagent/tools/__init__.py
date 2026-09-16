"""Tool package: base classes and the default registry."""

from .base import Tool, ToolRegistry
from .files import EditFile, Grep, ListDir, ReadFile, WriteFile
from .shell import RunCommand

__all__ = [
    "Tool",
    "ToolRegistry",
    "ListDir",
    "ReadFile",
    "WriteFile",
    "EditFile",
    "WriteFile",
    "Grep",
    "RunCommand",
]


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (ListDir(), ReadFile(), Grep(), WriteFile(), EditFile(), RunCommand()):
        registry.register(tool)
    return registry
