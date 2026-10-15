"""Put the terminal back the way the shell left it.

Mouse reporting is not enabled by iheye. iheye watches ``NSEvent.mouseLocation``
in its own process. The Textual TUI enables it: ``LinuxDriver.start_application_mode``
writes ``?1000h``, ``?1003h``, ``?1015h``, and ``?1006h`` (SGR), plus bracketed
paste (``?2004h``), the alternate screen (``?1049h``), and a hidden cursor
(``?25l``), and it saves termios.

``stop_application_mode`` turns mouse reporting off *before* it leaves the
alternate screen. macOS Terminal.app keeps SGR mode 1006 on the primary
screen when ``?1006l`` is sent only while the alt screen is up, so after
``ihagent`` exits every mouse move is echoed as ``35;col;rowM``. The reset
below sends those modes off, leaves the alt screen, then sends them off
again on the primary screen.

``install`` arms ``atexit`` and handlers for SIGINT, SIGTERM, and SIGHUP.
``restore`` is idempotent and is also called from ``finally`` blocks.
"""

from __future__ import annotations

import atexit
import os
import signal
import sys
from typing import Any

try:
    import termios
except ImportError:  # Windows has no termios; the escape sequences still apply.
    termios = None  # type: ignore[assignment]

# Order matches the modes Textual turns on, plus 1002 (button-event tracking),
# which some terminals enable alongside 1003.
_MOUSE_OFF = (
    "\x1b[?1000l"  # VT200 click tracking
    "\x1b[?1002l"  # button-event tracking
    "\x1b[?1003l"  # any-event tracking (motion)
    "\x1b[?1006l"  # SGR 1006 — the reports that leak as 35;col;rowM
    "\x1b[?1015l"  # urxvt
)
# Leave the alt screen, then disable mouse modes again. Terminal.app ignores
# ?1006l that was sent on the alternate screen.
_RESET = (
    _MOUSE_OFF
    + "\x1b[?2004l"  # bracketed paste
    + "\x1b[?25h"  # show cursor
    + "\x1b[?1049l"  # leave alternate screen
    + _MOUSE_OFF
)

_SIGNALS = tuple(
    sig
    for name in ("SIGINT", "SIGTERM", "SIGHUP")
    if (sig := getattr(signal, name, None)) is not None
)

_installed = False
_restored = False
_saved_fd: int | None = None
_saved_attrs: list | None = None
_previous: dict[int, Any] = {}


def reset_sequence() -> bytes:
    """Bytes that disable mouse reporting, bracketed paste, the alt screen, and the hidden cursor."""
    return _RESET.encode("ascii")


def install() -> None:
    """Remember the shell's termios and restore it on the way out. Safe to call twice."""
    global _installed
    _capture()
    if _installed:
        return
    _installed = True
    atexit.register(restore)
    for sig in _SIGNALS:
        try:
            _previous[sig] = signal.getsignal(sig)
            signal.signal(sig, _on_signal)
        except (OSError, ValueError):
            # Not the main thread, or the signal is unavailable.
            continue


def restore() -> None:
    """Disable mouse modes and put termios back. Never raises. A second call does nothing."""
    global _restored
    if _restored:
        return
    _restored = True
    try:
        _write_reset()
    except Exception:
        pass
    try:
        _restore_termios()
    except Exception:
        pass


def _on_signal(signum: int, frame: Any) -> None:
    """Restore the terminal, then keep the signal's previous behavior."""
    restore()
    previous = _previous.get(signum, signal.SIG_DFL)
    if previous == signal.SIG_IGN:
        return
    if callable(previous):
        previous(signum, frame)
        return
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def _capture() -> None:
    """Save termios once, before Textual switches the tty to raw mode."""
    global _saved_fd, _saved_attrs
    if _saved_attrs is not None or termios is None:
        return
    fd = _tty_fd()
    if fd is None:
        return
    try:
        _saved_attrs = termios.tcgetattr(fd)
        _saved_fd = fd
    except termios.error:
        _saved_attrs = None
        _saved_fd = None


def _restore_termios() -> None:
    if termios is None or _saved_attrs is None or _saved_fd is None:
        return
    termios.tcsetattr(_saved_fd, termios.TCSANOW, _saved_attrs)


def _write_reset() -> None:
    data = reset_sequence()
    fd = _tty_fd()
    opened: int | None = None
    if fd is None and _saved_attrs is not None:
        # stdio was redirected after we saved the shell's termios. The
        # controlling terminal is still the one Textual switched into raw mode.
        try:
            opened = os.open("/dev/tty", os.O_WRONLY | os.O_NOCTTY)
        except OSError:
            return
        fd = opened
    if fd is None:
        return
    try:
        _write_all(fd, data)
        if termios is not None:
            try:
                termios.tcdrain(fd)
            except termios.error:
                pass
    finally:
        if opened is not None:
            os.close(opened)


def _tty_fd() -> int | None:
    """A tty fd that is still the real terminal, even if ``sys.stdout`` was redirected."""
    for fd in (1, 2, 0):
        try:
            if os.isatty(fd):
                return fd
        except OSError:
            continue
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            fd = stream.fileno()
        except Exception:
            continue
        try:
            if os.isatty(fd):
                return fd
        except OSError:
            continue
    return None


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            return
        view = view[written:]
