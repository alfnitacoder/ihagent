"""Tool package: base classes and the default registry."""

from .base import Tool, ToolRegistry
from .browser import (
    BrowserClick,
    BrowserCloseTab,
    BrowserDownload,
    BrowserNewTab,
    BrowserOpen,
    BrowserPress,
    BrowserRead,
    BrowserScreenshot,
    BrowserScroll,
    BrowserSelect,
    BrowserSwitchTab,
    BrowserTabs,
    BrowserType,
    BrowserWait,
    WebSearch,
)
from .files import EditFile, Grep, ListDir, ReadFile, WriteFile
from .memory_tool import Remember
from .mouse import (
    MouseClick,
    MouseDescribe,
    MouseDoubleClick,
    MouseElements,
    MouseKey,
    MouseMove,
    MouseRightClick,
    MouseScroll,
    MouseType,
)
from .shell import RunCommand
from .ssh import SshRun
from .tests_tool import RunTests

__all__ = [
    "Tool",
    "ToolRegistry",
    "ListDir",
    "ReadFile",
    "WriteFile",
    "EditFile",
    "Grep",
    "RunCommand",
    "SshRun",
    "RunTests",
    "Remember",
    "WebSearch",
    "BrowserOpen",
    "BrowserTabs",
    "BrowserSwitchTab",
    "BrowserNewTab",
    "BrowserCloseTab",
    "BrowserRead",
    "BrowserClick",
    "BrowserType",
    "BrowserSelect",
    "BrowserPress",
    "BrowserScroll",
    "BrowserScreenshot",
    "BrowserWait",
    "BrowserDownload",
    "MouseElements",
    "MouseDescribe",
    "MouseClick",
    "MouseDoubleClick",
    "MouseRightClick",
    "MouseMove",
    "MouseScroll",
    "MouseType",
    "MouseKey",
]


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        ListDir(), ReadFile(), Grep(), WriteFile(), EditFile(), RunCommand(),
        SshRun(), RunTests(), Remember(),
        WebSearch(), BrowserOpen(), BrowserTabs(), BrowserSwitchTab(),
        BrowserNewTab(), BrowserCloseTab(), BrowserRead(), BrowserClick(),
        BrowserType(), BrowserSelect(), BrowserPress(), BrowserScroll(),
        BrowserScreenshot(), BrowserWait(), BrowserDownload(),
        MouseElements(), MouseDescribe(), MouseClick(), MouseDoubleClick(),
        MouseRightClick(), MouseMove(), MouseScroll(), MouseType(), MouseKey(),
    ):
        registry.register(tool)
    return registry
