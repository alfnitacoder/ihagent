"""Terminal restore: SGR mouse mode, alt screen, cursor, termios, signals."""

from __future__ import annotations

import signal
from types import SimpleNamespace

import pytest

from hfagent.cli import main
from hfagent.terminal import reset_sequence
from hfagent.ui.tui import run_tui
import hfagent.terminal as terminal


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(terminal, "_installed", False)
    monkeypatch.setattr(terminal, "_restored", False)
    monkeypatch.setattr(terminal, "_saved_fd", None)
    monkeypatch.setattr(terminal, "_saved_attrs", None)
    monkeypatch.setattr(terminal, "_previous", {})
    return terminal


def test_reset_sequence_disables_sgr_mouse_after_leaving_alt_screen():
    data = reset_sequence()
    for mode in (b"1000l", b"1002l", b"1003l", b"1006l", b"1015l"):
        assert b"\x1b[?" + mode in data
    assert b"\x1b[?2004l" in data  # bracketed paste
    assert b"\x1b[?25h" in data  # show cursor
    leave = data.find(b"\x1b[?1049l")
    assert leave > 0
    # macOS Terminal keeps 1006 unless it is turned off on the primary screen.
    assert data.rfind(b"\x1b[?1006l") > leave
    assert data.find(b"\x1b[?1006l") < leave


def test_restore_writes_reset_and_termios_once(fresh, monkeypatch):
    written: list[tuple[int, bytes]] = []
    saved = [0, 1, 2, 3, 4, 5, 6]
    seen: dict = {}

    def write(fd, data):
        written.append((fd, bytes(data)))
        return len(data)

    monkeypatch.setattr(terminal.os, "write", write)
    monkeypatch.setattr(terminal, "_tty_fd", lambda: 7)
    monkeypatch.setattr(terminal, "_saved_fd", 7)
    monkeypatch.setattr(terminal, "_saved_attrs", saved)
    monkeypatch.setattr(
        terminal.termios,
        "tcsetattr",
        lambda fd, when, attrs: seen.update(fd=fd, when=when, attrs=attrs),
    )
    monkeypatch.setattr(terminal.termios, "tcdrain", lambda fd: seen.setdefault("drain", fd))

    terminal.restore()
    blob = b"".join(chunk for _, chunk in written)
    assert written and written[0][0] == 7
    assert blob == reset_sequence()
    assert seen["fd"] == 7
    assert seen["attrs"] is saved
    assert seen["when"] == terminal.termios.TCSANOW
    assert seen["drain"] == 7

    terminal.restore()
    assert len(written) == 1


def test_restore_uses_dev_tty_when_stdio_was_redirected(fresh, monkeypatch):
    written: list[tuple[int, bytes]] = []
    monkeypatch.setattr(terminal, "_tty_fd", lambda: None)
    monkeypatch.setattr(terminal, "_saved_attrs", [0])
    monkeypatch.setattr(terminal, "_saved_fd", 0)
    monkeypatch.setattr(terminal.os, "open", lambda path, flags: 9)
    monkeypatch.setattr(terminal.os, "close", lambda fd: None)
    monkeypatch.setattr(
        terminal.os, "write", lambda fd, data: written.append((fd, bytes(data))) or len(data)
    )
    monkeypatch.setattr(terminal.termios, "tcsetattr", lambda *args: None)
    monkeypatch.setattr(terminal.termios, "tcdrain", lambda fd: None)
    terminal.restore()
    assert written and written[0][0] == 9
    assert b"\x1b[?1006l" in written[0][1]


def test_restore_without_a_tty_does_not_raise(fresh, monkeypatch):
    monkeypatch.setattr(terminal, "_tty_fd", lambda: None)
    monkeypatch.setattr(terminal, "_saved_attrs", None)

    def refuse(*_args, **_kwargs):
        raise AssertionError("restore should not open /dev/tty when no termios was saved")

    monkeypatch.setattr(terminal.os, "open", refuse)
    terminal.restore()


def test_install_registers_atexit_and_fatal_signals(fresh, monkeypatch):
    registered: list = []
    hooked: dict = {}
    monkeypatch.setattr(terminal.atexit, "register", lambda fn: registered.append(fn))
    monkeypatch.setattr(terminal.signal, "getsignal", lambda sig: signal.SIG_DFL)
    monkeypatch.setattr(terminal.signal, "signal", lambda sig, fn: hooked.__setitem__(sig, fn))
    monkeypatch.setattr(terminal, "_capture", lambda: None)

    terminal.install()
    terminal.install()

    assert registered == [terminal.restore]
    assert hooked[signal.SIGINT] is terminal._on_signal
    assert hooked[signal.SIGTERM] is terminal._on_signal
    assert hooked[signal.SIGHUP] is terminal._on_signal


def test_signal_handler_restores_then_chains(fresh, monkeypatch):
    order: list = []
    monkeypatch.setattr(terminal, "restore", lambda: order.append("restore"))

    def previous(signum, frame):
        order.append(("prev", signum))

    terminal._previous[signal.SIGINT] = previous
    terminal._on_signal(signal.SIGINT, None)
    assert order == ["restore", ("prev", signal.SIGINT)]


def test_signal_handler_leaves_an_ignored_signal_ignored(fresh, monkeypatch):
    order: list = []
    monkeypatch.setattr(terminal, "restore", lambda: order.append("restore"))
    monkeypatch.setattr(
        terminal.os, "kill", lambda pid, sig: order.append(("kill", sig))
    )
    terminal._previous[signal.SIGHUP] = signal.SIG_IGN
    terminal._on_signal(signal.SIGHUP, None)
    assert order == ["restore"]


def test_signal_handler_restores_then_reraises_default(fresh, monkeypatch):
    order: list = []
    monkeypatch.setattr(terminal, "restore", lambda: order.append("restore"))
    monkeypatch.setattr(
        terminal.signal, "signal", lambda sig, handler: order.append(("dfl", sig))
    )
    monkeypatch.setattr(
        terminal.os, "kill", lambda pid, sig: order.append(("kill", sig))
    )
    terminal._previous[signal.SIGTERM] = signal.SIG_DFL
    terminal._on_signal(signal.SIGTERM, None)
    assert order == [
        "restore",
        ("dfl", signal.SIGTERM),
        ("kill", signal.SIGTERM),
    ]


def test_main_restores_when_startup_crashes(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("hfagent.terminal.install", lambda: calls.append("install"))
    monkeypatch.setattr("hfagent.terminal.restore", lambda: calls.append("restore"))
    monkeypatch.setattr("hfagent.cli._stop_mcp", lambda: calls.append("stop-mcp"))
    monkeypatch.setattr("hfagent.cli._stop_iheye", lambda: calls.append("stop-eye"))
    monkeypatch.setattr("hfagent.cli._shutdown_browser", lambda: calls.append("stop-browser"))

    def boom(*_args, **_kwargs):
        raise RuntimeError("tui crashed")

    monkeypatch.setattr("hfagent.cli.make_agent", boom)
    with pytest.raises(RuntimeError, match="tui crashed"):
        main(["--console"])
    assert calls[0] == "install"
    assert "restore" in calls
    assert calls.index("restore") < calls.index("stop-mcp")


def test_run_tui_restores_on_quit_and_on_ctrl_c(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("hfagent.terminal.install", lambda: calls.append("install"))
    monkeypatch.setattr("hfagent.terminal.restore", lambda: calls.append("restore"))

    class Quiet:
        def run(self):
            calls.append("run")

    monkeypatch.setattr("hfagent.ui.tui.HfAgentApp", lambda _agent: Quiet())
    monkeypatch.setattr("hfagent.ui.tui.TuiBridge", lambda _app: object())
    run_tui(SimpleNamespace())
    assert calls == ["install", "run", "restore"]

    calls.clear()

    class Interrupted:
        def run(self):
            calls.append("run")
            raise KeyboardInterrupt

    monkeypatch.setattr("hfagent.ui.tui.HfAgentApp", lambda _agent: Interrupted())
    with pytest.raises(KeyboardInterrupt):
        run_tui(SimpleNamespace())
    assert calls == ["install", "run", "restore"]
