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
It saves termios on that first call, which is before Textual (or anything
else) switches the tty to raw mode. ``restore`` runs on the way out and is
idempotent. It must not run mid-session: ``--console`` keeps the shell's
line discipline, with ICRNL, ICANON, and ECHO forced on if a raw snapshot
was all we had, so Enter submits instead of echoing ``^M``.
"""

from __future__ import annotations

import atexit
import os
import select
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


def _copy_attrs(attrs: list) -> list:
    """Deep-copy a termios list so a later raw-mode edit cannot mutate the snapshot."""
    copied = list(attrs)
    if len(copied) > 6 and isinstance(copied[6], list):
        copied[6] = list(copied[6])
    return copied


def _attrs_to_apply(attrs: list) -> list:
    """Line-input flags for Enter. An already-cooked snapshot is returned as-is."""
    if termios is None or len(attrs) < 4:
        return attrs
    iflag = int(attrs[0])
    lflag = int(attrs[3])
    cooked = (
        (iflag & termios.ICRNL) != 0
        and (lflag & termios.ICANON) != 0
        and (lflag & termios.ECHO) != 0
    )
    if cooked:
        return attrs
    fixed = _copy_attrs(attrs)
    fixed[0] = iflag | termios.ICRNL
    fixed[0] &= ~(getattr(termios, "IGNCR", 0) | getattr(termios, "INLCR", 0))
    fixed[3] = lflag | termios.ICANON | termios.ECHO | termios.ISIG
    return fixed


def _capture() -> None:
    """Save termios once, before Textual switches the tty to raw mode."""
    global _saved_fd, _saved_attrs
    if _saved_attrs is not None or termios is None:
        return
    fd = _tty_fd()
    if fd is None:
        return
    try:
        _saved_attrs = _copy_attrs(termios.tcgetattr(fd))
        _saved_fd = fd
    except termios.error:
        _saved_attrs = None
        _saved_fd = None


def prepare_console() -> None:
    """Cooked input for ``--console``. Does not mark the terminal restored.

    Call this before reading a line, never as a substitute for ``restore``.
    Exit still puts the original snapshot back (with Enter's flags on if that
    snapshot was taken while the tty was already raw).
    """
    if termios is None:
        return
    fd = _tty_fd()
    if fd is None:
        return
    if _saved_attrs is None:
        _capture()
    if _saved_attrs is None:
        return
    try:
        termios.tcsetattr(fd, termios.TCSANOW, _attrs_to_apply(_saved_attrs))
    except termios.error:
        return


def _restore_termios() -> None:
    if termios is None or _saved_attrs is None or _saved_fd is None:
        return
    termios.tcsetattr(_saved_fd, termios.TCSANOW, _attrs_to_apply(_saved_attrs))


def split_submit_line(data: bytes) -> tuple[bytes | None, bytes]:
    """Split the first CR, LF, or CRLF. The break bytes are not part of the line."""
    for index, byte in enumerate(data):
        if byte not in (10, 13):
            continue
        end = index + 1
        if byte == 13 and end < len(data) and data[end] == 10:
            end += 1
        return data[:index], data[end:]
    return None, data


def _decode_submit_line(line: bytes) -> str:
    encoding = getattr(sys.stdin, "encoding", None) or "utf-8"
    try:
        text = line.decode(encoding, "surrogateescape")
    except LookupError:
        text = line.decode("utf-8", "surrogateescape")
    return text.strip()


def _commit_submit(pending: bytearray) -> str | None:
    raw = bytes(pending)
    line, rest = split_submit_line(raw)
    if line is None:
        return None
    bare_cr = raw[len(line)] == 13 and (
        len(line) + 1 >= len(raw) or raw[len(line) + 1] != 10
    )
    pending.clear()
    pending.extend(rest)
    if bare_cr:
        # ICRNL was off, so the tty echoed ^M instead of moving to the next row.
        try:
            sys.stdout.write("\n")
            sys.stdout.flush()
        except Exception:
            pass
    return _decode_submit_line(line)


def poll_submit_line(pending: bytearray, timeout: float, fd: int | None = None) -> str | None:
    """Return one CR- or LF-terminated line, or None if ``timeout`` elapses.

    Raises ``EOFError`` when the input stream closes. A bare CR submits, so
    macOS Terminal's Enter key works even if ICRNL is off.
    """
    ready_line = _commit_submit(pending)
    if ready_line is not None:
        return ready_line
    if fd is None:
        fd = sys.stdin.fileno()
    readable, _, _ = select.select([fd], [], [], timeout)
    if not readable:
        return None
    chunk = os.read(fd, 1024)
    if chunk == b"":
        if pending:
            text = _decode_submit_line(bytes(pending))
            pending.clear()
            return text
        raise EOFError
    pending.extend(chunk)
    return _commit_submit(pending)


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
