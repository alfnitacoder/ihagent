"""Decide whether the terminal hosting ihagent is the foreground window.

Windows uses ``GetForegroundWindow`` and ``GetWindowThreadProcessId`` and
compares that process with the terminal's process tree and console window
(including Windows Terminal, which owns the console window). Linux/X11 uses
``xdotool`` or EWMH when those tools answer. Wayland, and a machine with no
working probe, does not guess: the bubble stays visible unless a hotkey hides
it. Nothing here takes a screenshot.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

# Windows Terminal is one GUI process. Its console window's pid is that
# process, not the shell inside the tab.
WINDOWS_TERMINAL_EXES = frozenset(
    {
        "windowsterminal.exe",
        "wt.exe",
        "openconsole.exe",
    }
)

HOTKEY_LABEL = "Ctrl+Alt+I"


@dataclass(frozen=True)
class ForegroundWindow:
    hwnd: int = 0
    pid: int = 0
    exe: str = ""


@dataclass(frozen=True)
class FocusProbe:
    """``is_terminal`` is None when this desktop cannot be asked."""

    available: bool
    is_terminal: bool | None
    reason: str


def ancestor_pids(start_pid: int, parent_of, *, limit: int = 32) -> tuple[int, ...]:
    """Parents of ``start_pid``, nearest first. ``parent_of`` returns a pid or 0."""
    found: list[int] = []
    seen: set[int] = set()
    current = int(start_pid or 0)
    while len(found) < limit:
        if current <= 1 or current in seen:
            break
        seen.add(current)
        try:
            parent = int(parent_of(current) or 0)
        except Exception:
            break
        if parent <= 1 or parent == current:
            break
        found.append(parent)
        current = parent
    return tuple(found)


def ppid_from_proc_stat(text: str) -> int | None:
    """Parse ``/proc/<pid>/stat``. The command field may contain spaces."""
    raw = str(text or "")
    end = raw.rfind(")")
    if end < 0:
        return None
    tail = raw[end + 1 :].split()
    if len(tail) < 2:
        return None
    try:
        return int(tail[1])
    except ValueError:
        return None


def linux_parent_of(pid: int) -> int:
    try:
        with open(f"/proc/{int(pid)}/stat", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return 0
    parent = ppid_from_proc_stat(text)
    return int(parent or 0)


def terminal_pid_set(
    *,
    host_pid: int | None,
    ancestor_pids: tuple[int, ...] | list[int],
    console_pid: int = 0,
) -> set[int]:
    """Pids that count as the terminal. The helper's own pid is not included."""
    pids = {int(pid) for pid in ancestor_pids if int(pid) > 1}
    if host_pid:
        pids.add(int(host_pid))
    if console_pid and int(console_pid) > 1:
        pids.add(int(console_pid))
    return pids


def foreground_is_terminal(
    *,
    foreground_hwnd: int,
    foreground_pid: int,
    console_hwnd: int = 0,
    console_pid: int = 0,
    terminal_pids: set[int] | None = None,
    helper_pid: int = 0,
    foreground_exe: str = "",
) -> bool:
    """True when the foreground window is the terminal that hosts ihagent.

    A match on the console hwnd covers a classic console and the Windows
    Terminal window ``GetConsoleWindow`` returns. A match on the console
    pid covers Windows Terminal when another of its tabs is focused: every
    tab belongs to ``WindowsTerminal.exe``. The helper's own window is not
    the terminal, so hovering the bubble does not hide it.
    """
    fg_pid = int(foreground_pid or 0)
    if helper_pid and fg_pid == int(helper_pid):
        return False
    fg_hwnd = int(foreground_hwnd or 0)
    con_hwnd = int(console_hwnd or 0)
    if fg_hwnd and con_hwnd and fg_hwnd == con_hwnd:
        return True
    pids = set(terminal_pids or ())
    if console_pid and int(console_pid) > 1:
        pids.add(int(console_pid))
    if fg_pid and fg_pid in pids:
        return True
    exe = os.path.basename(str(foreground_exe or "")).lower()
    if exe in WINDOWS_TERMINAL_EXES and console_pid and fg_pid == int(console_pid):
        return True
    return False


def eye_visible(*, is_terminal: bool | None, manual_hidden: bool) -> bool:
    """Show the bubble unless the terminal is foreground, or a hotkey hid it.

    ``is_terminal is None`` means the desktop gave no answer (Wayland, or no
    probe). The bubble stays up until the hotkey hides it. ``manual_hidden``
    latches off until the hotkey is pressed again, including on Windows where
    focus detection otherwise works.
    """
    if manual_hidden:
        return False
    if is_terminal is None:
        return True
    return not is_terminal


def toggle_manual_hidden(hidden: bool, *, detection_ok: bool = False) -> bool:
    """Flip the hotkey latch. ``detection_ok`` is accepted and not required."""
    del detection_ok
    return not hidden


def window_pid(user32, hwnd: int) -> int:
    """``GetWindowThreadProcessId``. ``hwnd`` 0 yields 0 and does not call out."""
    handle = int(hwnd or 0)
    if handle <= 0:
        return 0
    slot = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(handle, ctypes.byref(slot))
    return int(slot.value)


def read_windows_foreground(user32, kernel32) -> tuple[ForegroundWindow, int, int]:
    """Foreground window plus the console hwnd and its process id.

    Calls ``GetForegroundWindow`` and ``GetWindowThreadProcessId``. Does not
    capture pixels.
    """
    fg_hwnd = int(user32.GetForegroundWindow() or 0)
    fg_pid = window_pid(user32, fg_hwnd)
    console_hwnd = int(kernel32.GetConsoleWindow() or 0)
    console_pid = window_pid(user32, console_hwnd)
    return ForegroundWindow(hwnd=fg_hwnd, pid=fg_pid), console_hwnd, console_pid


def windows_focus(
    *,
    foreground: ForegroundWindow,
    console_hwnd: int,
    console_pid: int,
    terminal_pids: set[int],
    helper_pid: int,
) -> FocusProbe:
    matched = foreground_is_terminal(
        foreground_hwnd=foreground.hwnd,
        foreground_pid=foreground.pid,
        console_hwnd=console_hwnd,
        console_pid=console_pid,
        terminal_pids=terminal_pids,
        helper_pid=helper_pid,
        foreground_exe=foreground.exe,
    )
    return FocusProbe(True, matched, "ok")


def linux_session_mode(environ: dict[str, str] | None = None) -> str:
    """``probe`` on X11 (including XWayland), ``wayland`` with no ``DISPLAY``, else ``unavailable``."""
    env = os.environ if environ is None else environ
    display = str(env.get("DISPLAY") or "").strip()
    wayland = str(env.get("WAYLAND_DISPLAY") or "").strip()
    if wayland and not display:
        return "wayland"
    if display or wayland:
        return "probe"
    return "unavailable"


def parse_xdotool_pid(text: str) -> int | None:
    """``xdotool getactivewindow getwindowpid`` prints one pid."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return None
    try:
        return int(lines[-1].split()[-1])
    except ValueError:
        return None


def parse_ewmh_window(text: str) -> str | None:
    """``xprop -root _NET_ACTIVE_WINDOW`` → a window id like ``0x3a00007``."""
    raw = str(text or "")
    marker = "window id #"
    if marker in raw:
        token = raw.split(marker, 1)[1].strip().split()
        if token:
            return token[0].rstrip(",")
    for token in raw.replace(",", " ").split():
        if token.startswith("0x"):
            return token
    return None


def parse_ewmh_pid(text: str) -> int | None:
    """``xprop -id <id> _NET_WM_PID`` → the cardinal pid."""
    raw = str(text or "")
    if "=" not in raw:
        return None
    tail = raw.split("=", 1)[1].strip()
    if not tail:
        return None
    try:
        return int(tail.split()[0])
    except ValueError:
        return None


def interpret_linux_focus(
    *,
    mode: str,
    active_pid: int | None,
    terminal_pids: set[int],
    helper_pid: int = 0,
) -> FocusProbe:
    if mode == "wayland":
        return FocusProbe(False, None, "wayland")
    if mode != "probe":
        return FocusProbe(False, None, "no-display")
    if active_pid is None or active_pid <= 0:
        return FocusProbe(False, None, "missing-tool")
    if helper_pid and int(active_pid) == int(helper_pid):
        return FocusProbe(True, False, "ok")
    return FocusProbe(True, int(active_pid) in terminal_pids, "ok")


def linux_active_pid(runner=None) -> int | None:
    """Pid of the focused X11 window, or None when the tools are missing.

    ``runner(argv)`` returns stdout. The default shells out to ``xdotool``,
    then ``xprop``. Failures become None so the caller can keep the bubble up.
    """
    if runner is None:
        runner = _run_tool
    xdotool = runner(["xdotool", "getactivewindow", "getwindowpid"])
    pid = parse_xdotool_pid(xdotool) if xdotool is not None else None
    if pid:
        return pid
    root = runner(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    if root is None:
        return None
    window = parse_ewmh_window(root)
    if not window:
        return None
    info = runner(["xprop", "-id", window, "_NET_WM_PID"])
    if info is None:
        return None
    return parse_ewmh_pid(info)


def _run_tool(argv: list[str]) -> str | None:
    import subprocess

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout or ""
