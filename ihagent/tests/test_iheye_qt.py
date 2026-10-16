"""Qt backend selection, foreground detection, and card placement.

These tests do not open a window. The paint check at the bottom renders
offscreen when PySide6 is installed.
"""

from __future__ import annotations

import ctypes

import pytest

from hfagent.iheye.backend import backend_name, backend_status, qt_available
from hfagent.iheye.foreground import (
    ancestor_pids,
    eye_visible,
    foreground_is_terminal,
    interpret_linux_focus,
    linux_session_mode,
    parse_ewmh_pid,
    parse_ewmh_window,
    parse_xdotool_pid,
    ppid_from_proc_stat,
    read_windows_foreground,
    terminal_pid_set,
    toggle_manual_hidden,
    window_pid,
)
from hfagent.iheye.host import HostInfo
from hfagent.iheye.look import capture_explicit_window
from hfagent.iheye.qt_geometry import (
    QtRect,
    place_card,
    primary_bottom,
    qt_screen_to_display,
    same_monitor,
)
from hfagent.iheye.supervisor import EyeSupervisor


def _host(**kwargs) -> HostInfo:
    base = dict(
        kind="terminal",
        app_name="Windows Terminal",
        bundle_id="",
        pid=400,
        ancestor_pids=(500, 400),
        tty="",
        source="parent",
    )
    base.update(kwargs)
    return HostInfo(**base)


class _User32:
    def __init__(self, windows: dict[int, int], foreground: int) -> None:
        self.windows = windows
        self.foreground = foreground
        self.calls: list[str] = []

    def GetForegroundWindow(self):
        self.calls.append("GetForegroundWindow")
        return self.foreground

    def GetWindowThreadProcessId(self, hwnd, slot):
        self.calls.append("GetWindowThreadProcessId")
        slot._obj.value = self.windows.get(int(hwnd), 0)
        return 1


class _Kernel32:
    def __init__(self, console: int) -> None:
        self.console = console

    def GetConsoleWindow(self):
        return self.console


def test_backend_names():
    assert backend_name("darwin") == "pyobjc"
    assert backend_name("win32") == "qt"
    assert backend_name("linux") == "qt"
    assert backend_name("freebsd") is None
    assert isinstance(qt_available(), bool)


def test_missing_pyside_does_not_spawn(monkeypatch, tmp_path):
    monkeypatch.setattr("hfagent.iheye.backend.qt_available", lambda: False)
    calls = []
    monkeypatch.setattr(
        "hfagent.iheye.supervisor.subprocess.Popen",
        lambda *args, **kwargs: calls.append(1),
    )
    control = EyeSupervisor(
        socket_dir=tmp_path,
        position_path=tmp_path / "pos.json",
        log_path=tmp_path / "iheye.log",
        platform_name="win32",
    )
    status = control.start(_host())
    assert status.running is False
    assert status.reason == "missing-pyside"
    assert "PySide6" in status.message
    assert calls == []
    ready, reason, _message = backend_status("darwin")
    assert reason in {"pyobjc", "missing-pyobjc"}
    assert ready is (reason == "pyobjc")


def test_qt_backend_spawns_the_same_helper(monkeypatch, tmp_path):
    monkeypatch.setattr("hfagent.iheye.backend.qt_available", lambda: True)

    class Proc:
        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            return None

        def kill(self):
            return None

    spawned = {}

    def fake_popen(cmd, **kwargs):
        spawned["cmd"] = cmd
        return Proc()

    monkeypatch.setattr("hfagent.iheye.supervisor.subprocess.Popen", fake_popen)
    control = EyeSupervisor(
        socket_dir=tmp_path,
        position_path=tmp_path / "pos.json",
        log_path=tmp_path / "iheye.log",
        platform_name="linux",
    )
    status = control.start(_host(), parent_pid=123)
    assert status.running
    assert spawned["cmd"][1:3] == ["-m", "hfagent.iheye"]
    control.stop()


def test_windows_foreground_uses_the_console_window_and_process_id():
    # Windows Terminal owns hwnd 10. The shell (400) is the host pid.
    user = _User32({10: 500, 77: 900}, foreground=10)
    kernel = _Kernel32(console=10)
    foreground, console_hwnd, console_pid = read_windows_foreground(user, kernel)
    assert user.calls[0] == "GetForegroundWindow"
    assert "GetWindowThreadProcessId" in user.calls
    assert foreground.hwnd == 10
    assert foreground.pid == 500
    assert console_hwnd == 10
    assert console_pid == 500
    pids = terminal_pid_set(host_pid=400, ancestor_pids=(500, 400), console_pid=console_pid)
    assert foreground_is_terminal(
        foreground_hwnd=foreground.hwnd,
        foreground_pid=foreground.pid,
        console_hwnd=console_hwnd,
        console_pid=console_pid,
        terminal_pids=pids,
        helper_pid=42,
        foreground_exe="WindowsTerminal.exe",
    )


def test_windows_foreground_ignores_the_helper_and_other_apps():
    user = _User32({10: 500, 3: 42, 8: 900}, foreground=8)
    kernel = _Kernel32(console=10)
    foreground, console_hwnd, console_pid = read_windows_foreground(user, kernel)
    pids = terminal_pid_set(host_pid=400, ancestor_pids=(500,), console_pid=console_pid)
    assert not foreground_is_terminal(
        foreground_hwnd=foreground.hwnd,
        foreground_pid=foreground.pid,
        console_hwnd=console_hwnd,
        console_pid=console_pid,
        terminal_pids=pids,
        helper_pid=42,
    )
    # The bubble itself is foreground. Do not treat that as "back in the terminal".
    user.foreground = 3
    foreground, console_hwnd, console_pid = read_windows_foreground(user, kernel)
    assert not foreground_is_terminal(
        foreground_hwnd=foreground.hwnd,
        foreground_pid=foreground.pid,
        console_hwnd=console_hwnd,
        console_pid=console_pid,
        terminal_pids=pids,
        helper_pid=42,
    )


def test_window_pid_does_not_call_for_a_null_hwnd():
    calls = []

    class API:
        def GetWindowThreadProcessId(self, hwnd, slot):
            calls.append(hwnd)

    assert window_pid(API(), 0) == 0
    assert calls == []
    slot_pid = ctypes.c_ulong(0)

    class Writer:
        def GetWindowThreadProcessId(self, hwnd, slot):
            slot._obj.value = 17
            return 1

    assert window_pid(Writer(), 4) == 17
    assert slot_pid.value == 0


def test_ancestor_walk_and_proc_stat():
    parents = {8: 7, 7: 6, 6: 1}
    assert ancestor_pids(8, parents.get) == (7, 6)
    stat = "8 (Windows Terminal) S 7 1 1 0"
    assert ppid_from_proc_stat(stat) == 7
    assert ppid_from_proc_stat("nope") is None


def test_linux_x11_hides_for_the_terminal_and_wayland_stays_visible():
    assert linux_session_mode({"WAYLAND_DISPLAY": "wayland-0"}) == "wayland"
    assert linux_session_mode({"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}) == "probe"
    assert linux_session_mode({}) == "unavailable"
    assert parse_xdotool_pid("4242\n") == 4242
    assert parse_ewmh_window("_NET_ACTIVE_WINDOW(WINDOW): window id # 0x3a00007") == "0x3a00007"
    assert parse_ewmh_pid("_NET_WM_PID(CARDINAL) = 4242") == 4242
    pids = {4242, 50}
    focused = interpret_linux_focus(mode="probe", active_pid=4242, terminal_pids=pids, helper_pid=9)
    assert focused.available and focused.is_terminal is True
    other = interpret_linux_focus(mode="probe", active_pid=80, terminal_pids=pids, helper_pid=9)
    assert other.is_terminal is False
    wayland = interpret_linux_focus(mode="wayland", active_pid=None, terminal_pids=pids, helper_pid=9)
    assert wayland.is_terminal is None
    assert eye_visible(is_terminal=None, manual_hidden=False) is True
    assert eye_visible(is_terminal=True, manual_hidden=False) is False
    assert eye_visible(is_terminal=False, manual_hidden=False) is True
    assert eye_visible(is_terminal=False, manual_hidden=True) is False
    assert toggle_manual_hidden(False, detection_ok=False) is True
    assert toggle_manual_hidden(True, detection_ok=True) is False
    assert eye_visible(is_terminal=None, manual_hidden=True) is False


def test_capture_runs_only_when_the_hover_send_asked():
    grabs = []

    def grab(hwnd, path):
        grabs.append((hwnd, path))
        from hfagent.iheye.look import ShotResult

        return ShotResult(True, "", 200)

    host = _host()
    skipped = capture_explicit_window(
        requested=False,
        hwnd=55,
        pid=80,
        title="Notes",
        host=host,
        helper_pid=9,
        image_path="/tmp/hfagent-iheye-look-x.png",
        grab=grab,
    )
    assert skipped is None
    assert grabs == []
    shot = capture_explicit_window(
        requested=True,
        hwnd=55,
        pid=80,
        title="Notes",
        host=host,
        helper_pid=9,
        image_path="/tmp/hfagent-iheye-look-x.png",
        grab=grab,
    )
    assert shot is not None and shot.image.endswith("hfagent-iheye-look-x.png")
    assert grabs == [(55, "/tmp/hfagent-iheye-look-x.png")]
    own = capture_explicit_window(
        requested=True,
        hwnd=0,
        pid=80,
        title="Notes",
        host=host,
        helper_pid=9,
        image_path="/tmp/hfagent-iheye-look-x.png",
        grab=grab,
    )
    assert own is not None and own.skipped == "no-window"
    assert len(grabs) == 1


def test_card_stays_on_the_bubbles_monitor():
    primary = QtRect(0, 0, 1920, 1080)
    left = QtRect(-1920, 0, 1920, 1080)
    bottom = primary_bottom(primary)
    primary_display = qt_screen_to_display(primary, primary, bottom)
    left_display = qt_screen_to_display(left, left, bottom)
    bubble = QtRect(1700, 80, 168, 220)
    card = place_card(bubble, 280, 176, primary_display.visible, bottom)
    assert same_monitor(bubble, card, [primary, left])
    assert card.x >= 0
    moved = QtRect(-1800, 80, 168, 220)
    other = place_card(moved, 280, 176, left_display.visible, bottom)
    assert same_monitor(moved, other, [primary, left])
    assert other.x < 0


def test_qt_skins_render_offscreen(tmp_path):
    pytest.importorskip("PySide6")
    from hfagent.iheye.qt_paint import render_skin_image

    for skin in ("bot", "classic", "grok", "robot"):
        image = render_skin_image(skin, state="idle", phase=1.2)
        assert image.width() > 40 and image.height() > 40
        path = tmp_path / f"{skin}.png"
        assert image.save(str(path), "PNG")
        assert path.stat().st_size > 500
    working = render_skin_image("bot", state="thinking", phase=2.0, working=True)
    assert working.width() > 40
