"""Cross-platform pointer. pyautogui does the click; pynput types Unicode.

Neither library is imported until a mouse action actually runs.
"""

from __future__ import annotations

import sys

from .permissions import MISSING_DESKTOP
from .session import EmergencyStop


class MissingDesktopExtra(Exception):
    def __init__(self) -> None:
        super().__init__(MISSING_DESKTOP)


class PyAutoPointer:
    """The real pointer. ``FAILSAFE`` stays on so a corner raises."""

    def __init__(self) -> None:
        self._pyautogui = None

    def position(self) -> tuple[float, float]:
        point = self._lib().position()
        return float(point.x), float(point.y)

    def move(self, x: float, y: float) -> None:
        self._call(lambda gui: gui.moveTo(int(round(x)), int(round(y))))

    def click(self, x: float, y: float, button: str = "left", clicks: int = 1) -> None:
        self._call(
            lambda gui: gui.click(
                int(round(x)),
                int(round(y)),
                clicks=int(clicks),
                button=button,
            )
        )

    def scroll(self, x: float, y: float, amount: int) -> None:
        # Positive amount scrolls up, matching pyautogui.scroll.
        self._call(
            lambda gui: gui.scroll(int(amount), x=int(round(x)), y=int(round(y)))
        )

    def type_text(self, text: str) -> None:
        try:
            from pynput.keyboard import Controller

            Controller().type(text)
            return
        except ImportError:
            pass
        except Exception:
            pass
        self._call(lambda gui: gui.write(text, interval=0.01))

    def press(self, keys: list[str]) -> None:
        if not keys:
            return
        if len(keys) == 1:
            self._call(lambda gui: gui.press(keys[0]))
            return
        self._call(lambda gui: gui.hotkey(*keys))

    def _lib(self):
        if self._pyautogui is None:
            try:
                import pyautogui
            except ImportError as exc:
                raise MissingDesktopExtra from exc
            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0.05
            self._pyautogui = pyautogui
        return self._pyautogui

    def _call(self, fn) -> None:
        gui = self._lib()
        try:
            fn(gui)
        except Exception as exc:
            if exc.__class__.__name__ == "FailSafeException":
                raise EmergencyStop("pointer in a screen corner") from exc
            raise


def normalize_keys(spec: str, platform_name: str | None = None) -> list[str]:
    """``ctrl+s`` to pyautogui key names. ``command`` becomes ``ctrl`` off macOS."""
    system = platform_name if platform_name is not None else sys.platform
    aliases = {
        "control": "ctrl",
        "ctl": "ctrl",
        "option": "alt",
        "opt": "alt",
        "escape": "esc",
        "return": "enter",
        "del": "delete",
        "cmd": "command",
        "meta": "command",
        "super": "command",
        "win": "win",
    }
    keys: list[str] = []
    for raw in (spec or "").split("+"):
        name = raw.strip().lower()
        if not name:
            continue
        name = aliases.get(name, name)
        if name == "command" and system != "darwin":
            name = "ctrl"
        keys.append(name)
    return keys
