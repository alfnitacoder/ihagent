"""Which iheye backend this platform should run.

macOS keeps the PyObjC overlay. Windows and Linux use the Qt (PySide6)
overlay. Nothing here imports AppKit or PySide6; availability is a spec check
so the supervisor can fail with a hint before it spawns a helper.
"""

from __future__ import annotations

import importlib.util

INSTALL_QT_HINT = "pip install 'hfagent[iheye]'"

_QT_PLATFORMS = frozenset({"win32", "linux"})


def backend_name(platform_name: str) -> str | None:
    """``pyobjc`` on macOS, ``qt`` on Windows and Linux, otherwise ``None``."""
    if platform_name == "darwin":
        return "pyobjc"
    if platform_name in _QT_PLATFORMS:
        return "qt"
    return None


def qt_available() -> bool:
    try:
        return importlib.util.find_spec("PySide6") is not None
    except (ImportError, ValueError):
        return False


def backend_status(platform_name: str) -> tuple[bool, str, str]:
    """Whether the helper can start.

    Returns ``(ready, reason, message)``. ``reason`` is ``pyobjc``, ``qt``,
    ``missing-pyobjc``, ``missing-pyside``, or ``unsupported``.
    """
    name = backend_name(platform_name)
    if name == "pyobjc":
        from .supervisor import INSTALL_HINT, pyobjc_available

        if not pyobjc_available():
            return False, "missing-pyobjc", f"iheye needs PyObjC. {INSTALL_HINT}"
        return True, "pyobjc", ""
    if name == "qt":
        if not qt_available():
            return False, "missing-pyside", f"iheye needs PySide6. {INSTALL_QT_HINT}"
        return True, "qt", ""
    return False, "unsupported", "iheye runs on macOS, Windows, and Linux"
