"""Messages for a missing extra or a missing OS permission.

These are the whole answer the model and the user see. They name the
setting to change and do not include a stack trace.
"""

from __future__ import annotations

MISSING_DESKTOP = (
    "Error: mouse control needs the optional desktop extra. "
    "Install it with: pip install 'ihagent[desktop]'"
)

MAC_AX_INSTALL = (
    "Error: reading the macOS accessibility tree needs PyObjC. "
    "Install the desktop extra: pip install 'ihagent[desktop]'"
)

MAC_AX_HINT = (
    "Error: macOS Accessibility permission is off, so the focused app's "
    "element tree cannot be read. Open System Settings → Privacy & Security "
    "→ Accessibility and enable the Python that runs ihagent (the venv's "
    "python, or Terminal if that is what macOS lists). Then try again."
)

MAC_SCREEN_HINT = (
    "Error: looking at this window needs Screen Recording permission "
    "(System Settings → Privacy & Security → Screen Recording). "
    "Enable the Python that runs ihagent."
)

WIN_UIA_HINT = (
    "Error: Windows UI Automation is not available. Install the desktop "
    "extra: pip install \"ihagent[desktop]\" (it includes pywinauto). If it "
    "is already installed, the focused app may not expose UI Automation, "
    "and a click can fall back to a screenshot when a vision model is set."
)

LINUX_ATSPI_HINT = (
    "Error: Linux accessibility (AT-SPI) is not available, so the focused "
    "app's element tree cannot be read. Install the system package "
    "(Debian/Ubuntu: sudo apt install python3-pyatspi at-spi2-core; "
    "Fedora: sudo dnf install python3-pyatspi at-spi2-core) and use a "
    "session whose accessibility bus is running. A click can still fall "
    "back to a screenshot if a vision model is configured."
)


def screen_hint(platform_name: str) -> str:
    if platform_name == "darwin":
        return MAC_SCREEN_HINT
    if platform_name == "win32":
        return (
            "Error: the screenshot failed. On Windows, confirm this process "
            "can capture the screen and that the desktop extra is installed "
            "(pip install \"ihagent[desktop]\")."
        )
    return (
        "Error: the screenshot failed. On Linux, install the desktop extra "
        "(pip install 'ihagent[desktop]') and a capture tool such as "
        "gnome-screenshot or grim. Wayland sessions sometimes block capture "
        "until a portal permission is granted."
    )
