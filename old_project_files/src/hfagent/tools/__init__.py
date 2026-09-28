"""Tool package: base classes and the default registry."""

from .base import Tool, ToolRegistry
from .files import EditFile, Grep, ListDir, ReadFile, WriteFile
from .memory_tool import Remember
from .shell import RunCommand
from .ssh import SshRun

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
    "SshRun",
    "Remember",
]


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        ListDir(), ReadFile(), Grep(), WriteFile(), EditFile(), RunCommand(),
        SshRun(), Remember(),
    ):
        registry.register(tool)
    return registry
