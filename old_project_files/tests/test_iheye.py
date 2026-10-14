"""iheye overlay: host detection, focus rules, IPC, and the helper supervisor."""

import os
import subprocess
import time

import pytest

from hfagent.agent import Agent, AgentUI
from hfagent.commands import HELP, dispatch_slash
from hfagent.config import Config, format_config_report
from hfagent.iheye import (
    INSTALL_HINT,
    enabled_from_env,
    handle_command,
    publish,
    start_if_enabled,
    status_text,
)
from hfagent.iheye.appearance import (
    blink_amount,
    eye_geometry,
    pulse_alpha,
    pupil_offset,
)
from hfagent.iheye.focus import FrontApp, activation_plan, eye_should_show, same_host
from hfagent.iheye.host import (
    HostInfo,
    ProcessRow,
    current_tty,
    detect_host,
    focus_script,
    front_tty_script,
    parse_ps_line,
    run_osascript,
    same_tty,
)
from hfagent.iheye.lifecycle import helper_should_exit, pid_alive
from hfagent.iheye.position import (
    clamp_position,
    default_position,
    load_position,
    save_position,
)
from hfagent.iheye.protocol import EyeServer, connect_socket, decode, encode
from hfagent.iheye.supervisor import EyeSupervisor, helper_command, pyobjc_available
from hfagent.iheye.__main__ import host_from_args, main as helper_main
from hfagent.tools import default_registry


def _lookup(chain):
    table = {
        pid: ProcessRow(ppid, comm, command) for pid, ppid, comm, command in chain
    }

    def lookup(pid):
        return table.get(pid)

    return lookup


def _host(**kwargs):
    base = dict(
        kind="terminal",
        app_name="Terminal",
        bundle_id="com.apple.Terminal",
        pid=200,
        ancestor_pids=(300, 200),
        tty="/dev/ttys005",
        source="parent",
    )
    base.update(kwargs)
    return HostInfo(**base)


def test_enabled_defaults_to_macos_only():
    assert enabled_from_env({}, "darwin") is True
    assert enabled_from_env({}, "linux") is False
    assert enabled_from_env({"HFAGENT_IHEYE": "1"}, "linux") is True
    assert enabled_from_env({"HFAGENT_IHEYE": "0"}, "darwin") is False
    for off in ("off", "false", "no", "NO"):
        assert enabled_from_env({"HFAGENT_IHEYE": off}, "darwin") is False
    for on in ("on", "true", "yes", "TRUE"):
        assert enabled_from_env({"HFAGENT_IHEYE": on}, "linux") is True


def test_parent_chain_finds_known_terminals():
    cases = [
        (
            [
                (300, 200, "zsh", "-zsh"),
                (200, 1, "Terminal", "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal"),
            ],
            "terminal",
            "com.apple.Terminal",
            200,
        ),
        (
            [
                (10, 9, "fish", "fish"),
                (9, 1, "iTerm2", "/Applications/iTerm.app/Contents/MacOS/iTerm2"),
            ],
            "iterm2",
            "com.googlecode.iterm2",
            9,
        ),
        (
            [
                (50, 40, "zsh", "zsh"),
                (40, 1, "stable", "/Applications/Warp.app/Contents/MacOS/stable"),
            ],
            "warp",
            "dev.warp.Warp-Stable",
            40,
        ),
        (
            [
                (300, 250, "zsh", "zsh"),
                (250, 100, "Code", "/Applications/Visual Studio Code.app/Contents/MacOS/Code Helper"),
                (100, 1, "Code", "/Applications/Visual Studio Code.app/Contents/MacOS/Code"),
            ],
            "vscode",
            "com.microsoft.VSCode",
            100,
        ),
        (
            [
                (300, 250, "zsh", "-zsh"),
                (250, 180, "Cursor", "/Applications/Cursor.app/Contents/MacOS/Cursor Helper"),
                (180, 1, "Cursor", "/Applications/Cursor.app/Contents/MacOS/Cursor"),
            ],
            "cursor",
            "com.todesktop.230313mzl4w4u92",
            180,
        ),
        (
            [
                (4, 3, "zsh", "zsh"),
                (3, 1, "ghostty", "/Applications/Ghostty.app/Contents/MacOS/ghostty"),
            ],
            "ghostty",
            "com.mitchellh.ghostty",
            3,
        ),
        (
            [(4, 3, "zsh", "zsh"), (3, 1, "kitty", "/Applications/kitty.app/Contents/MacOS/kitty")],
            "kitty",
            "net.kovidgoyal.kitty",
            3,
        ),
        (
            [(4, 3, "zsh", "zsh"), (3, 1, "wezterm-gui", "/Applications/WezTerm.app/Contents/MacOS/wezterm-gui")],
            "wezterm",
            "com.github.wez.wezterm",
            3,
        ),
        (
            [(4, 3, "zsh", "zsh"), (3, 1, "alacritty", "/Applications/Alacritty.app/Contents/MacOS/alacritty")],
            "alacritty",
            "org.alacritty",
            3,
        ),
    ]
    for chain, kind, bundle, pid in cases:
        host = detect_host(
            pid=chain[0][0],
            lookup=_lookup(chain),
            environ={"TERM_PROGRAM": "vscode"},
            tty="/dev/ttys005",
        )
        assert host.kind == kind, kind
        assert host.bundle_id == bundle
        assert host.pid == pid
        assert host.tty == "/dev/ttys005"
        assert host.source == "parent"
        assert pid in host.ancestor_pids


def test_cursor_env_beats_vscode_term_program_when_process_tree_is_silent():
    host = detect_host(
        pid=1,
        lookup=lambda _pid: None,
        environ={
            "TERM_PROGRAM": "vscode",
            "VSCODE_GIT_ASKPASS_NODE": "/Applications/Cursor.app/Contents/Resources/app/bin/code",
        },
        tty="/dev/ttys002",
    )
    assert host.kind == "cursor"
    assert host.pid is None
    assert host.source == "environ"


def test_term_program_and_env_fallbacks():
    iterm = detect_host(
        pid=1,
        lookup=lambda _pid: None,
        environ={"TERM_PROGRAM": "iTerm.app"},
        tty="/dev/ttys001",
    )
    assert iterm.kind == "iterm2"
    assert iterm.bundle_id == "com.googlecode.iterm2"

    by_session = detect_host(
        pid=1,
        lookup=lambda _pid: None,
        environ={"ITERM_SESSION_ID": "w1t1p0"},
        tty="",
    )
    assert by_session.kind == "iterm2"

    warp = detect_host(
        pid=1,
        lookup=lambda _pid: None,
        environ={"TERM_PROGRAM": "WarpTerminal"},
        tty="",
    )
    assert warp.kind == "warp"

    ghostty = detect_host(
        pid=1,
        lookup=lambda _pid: None,
        environ={"GHOSTTY_RESOURCES_DIR": "/Applications/Ghostty.app"},
        tty="",
    )
    assert ghostty.kind == "ghostty"


def test_unknown_gui_parent_is_still_recorded():
    chain = [
        (9, 8, "bash", "bash"),
        (8, 1, "MyTerm", "/Applications/MyTerm.app/Contents/MacOS/MyTerm"),
    ]
    host = detect_host(pid=9, lookup=_lookup(chain), environ={}, tty="/dev/ttys009")
    assert host.kind == "unknown"
    assert host.pid == 8
    assert host.app_name == "MyTerm"
    assert host.source == "ancestor"


def test_parse_ps_line():
    row = parse_ps_line(
        "  200 Terminal /System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal"
    )
    assert row is not None
    assert row.ppid == 200
    assert row.comm == "Terminal"
    assert "Terminal.app" in row.command
    assert parse_ps_line("nope") is None


def test_eye_hides_for_the_host_app_and_shows_for_other_windows():
    host = _host()
    terminal = FrontApp(200, "com.apple.Terminal", "Terminal")
    safari = FrontApp(999, "com.apple.Safari", "Safari")
    assert eye_should_show(safari, host, None) is True
    assert eye_should_show(None, host, None) is True
    assert eye_should_show(terminal, host, None) is False
    assert eye_should_show(terminal, host, "/dev/ttys005") is False
    assert eye_should_show(terminal, host, "/dev/ttys009") is True
    assert eye_should_show(terminal, host, "") is False

    code = _host(
        kind="vscode",
        app_name="Code",
        bundle_id="com.microsoft.VSCode",
        pid=100,
        ancestor_pids=(300, 250, 100),
    )
    front_code = FrontApp(100, "com.microsoft.VSCode", "Code")
    assert eye_should_show(front_code, code, "/dev/ttys999") is False
    assert same_host(FrontApp(250, "com.microsoft.VSCode", "Code Helper"), code)


def test_focus_scripts_are_limited_to_terminal_and_iterm():
    assert "selected tab" in front_tty_script("terminal")
    assert "current session" in front_tty_script("iterm2")
    assert front_tty_script("vscode") is None
    assert front_tty_script("warp") is None

    script = focus_script("terminal", "/dev/ttys003")
    assert script is not None and "/dev/ttys003" in script
    iterm = focus_script("iterm2", "/dev/ttys003")
    assert iterm is not None and "iTerm2" in iterm
    assert focus_script("vscode", "/dev/ttys003") is None
    assert focus_script("terminal", "") is None
    escaped = focus_script("terminal", 'odd"tty')
    assert escaped is not None and 'odd\\"tty' in escaped

    plan = activation_plan(_host())
    assert plan.applescript is not None
    assert plan.bundle_id == "com.apple.Terminal"
    quiet = activation_plan(_host(kind="vscode", app_name="Code", bundle_id="com.microsoft.VSCode", tty="/dev/ttys1"))
    assert quiet.applescript is None

    assert run_osascript("ignored", runner=lambda _script: "  /dev/ttys003 \n") == "/dev/ttys003"
    assert run_osascript("ignored", runner=lambda _script: (_ for _ in ()).throw(RuntimeError("x"))) == ""
    assert same_tty("/dev/ttys003", "/dev/ttys003")
    assert same_tty("ttys003", "/dev/ttys003")
    assert not same_tty("ttys003", "/dev/ttys009")
    assert current_tty() == "" or current_tty().startswith("/dev/")


def test_eye_motion_reflects_state():
    assert pulse_alpha("waiting", 0.3) > pulse_alpha("thinking", 0.3)
    assert pulse_alpha("thinking", 0.3) > pulse_alpha("idle", 1.0)
    assert pulse_alpha("done", 0.4) > 0.4
    assert pulse_alpha("done", 100) == 0.0
    assert blink_amount("waiting", 0.1) == 0.0
    assert blink_amount("done", 0.1) == 0.0
    assert blink_amount("idle", 0.1) > 0.0
    assert blink_amount("idle", 3.0) == 0.0
    thinking = pupil_offset("thinking", 1.2)
    idle = pupil_offset("idle", 1.2)
    assert abs(thinking[0]) > abs(idle[0])
    geo = eye_geometry("thinking", 1.2)
    px, py, pr = geo.pupil
    icx, icy, ir = geo.iris
    assert (px - icx) ** 2 + (py - icy) ** 2 < (ir + pr) ** 2
    assert geo.state == "thinking"
    assert eye_geometry("nope", 0).state == "idle"


def test_position_memory(tmp_path):
    path = tmp_path / "iheye-position.json"
    assert load_position(path) is None
    save_position(path, 12.5, 40)
    assert load_position(path) == (12.5, 40.0)
    path.write_text("not-json", encoding="utf-8")
    assert load_position(path) is None
    assert default_position(0, 0, 1440, 900, 88, 100, margin=18) == (1334.0, 782.0)
    assert clamp_position(9999, -20, 0, 0, 800, 600, 88, 100) == (712.0, 0.0)
    assert clamp_position(10, 10, 0, 0, 40, 40, 88, 100) == (0.0, 0.0)


def test_ipc_roundtrip(tmp_path):
    path = tmp_path / "eye.sock"
    got = []
    server = EyeServer(str(path), got.append)
    server.start()
    try:
        client = connect_socket(str(path), timeout=2)
        client.sendall(encode({"state": "waiting"}))
        client.sendall(encode({"cmd": "quit"}))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and len(got) < 2:
            time.sleep(0.02)
        client.close()
    finally:
        server.stop()
    assert got[0]["state"] == "waiting"
    assert got[1]["cmd"] == "quit"
    assert decode(b"") is None
    assert decode(b"not json") is None
    assert decode(b"[1]") is None
    assert encode({"state": "idle"}).endswith(b"\n")


def test_helper_exits_when_parent_is_gone():
    assert helper_should_exit(quit_requested=True, parent_alive=True)
    assert helper_should_exit(quit_requested=False, parent_alive=False)
    assert not helper_should_exit(quit_requested=False, parent_alive=True)
    assert pid_alive(os.getpid())
    assert not pid_alive(0)
    assert not pid_alive(-1)


def test_helper_command_and_argv_roundtrip():
    host = _host()
    cmd = helper_command(
        parent_pid=42,
        socket_path="/tmp/eye.sock",
        position_path="/tmp/pos.json",
        host=host,
        executable="/usr/bin/python",
    )
    assert cmd[:3] == ["/usr/bin/python", "-m", "hfagent.iheye"]
    assert cmd[cmd.index("--parent-pid") + 1] == "42"
    assert "--tty" in cmd and "/dev/ttys005" in cmd
    from hfagent.iheye.__main__ import build_parser

    args = build_parser().parse_args(cmd[3:])
    restored = host_from_args(args)
    assert restored.kind == "terminal"
    assert restored.pid == 200
    assert restored.ancestor_pids == (300, 200)
    assert restored.tty == "/dev/ttys005"


def test_helper_main_is_a_noop_off_macos(monkeypatch):
    monkeypatch.setattr("hfagent.iheye.__main__.sys.platform", "linux")
    assert (
        helper_main(
            ["--parent-pid", "1", "--socket", "/tmp/iheye.sock", "--position", "/tmp/pos.json"]
        )
        == 0
    )


def test_start_is_a_noop_when_disabled_or_not_macos(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "hfagent.iheye.supervisor.subprocess.Popen",
        lambda *args, **kwargs: calls.append(args),
    )
    control = EyeSupervisor(
        socket_dir=tmp_path,
        position_path=tmp_path / "pos.json",
        log_path=tmp_path / "iheye.log",
        platform_name="linux",
    )
    status = start_if_enabled(environ={}, platform_name="linux", control=control, host=_host())
    assert status.reason == "disabled"
    assert calls == []

    forced = start_if_enabled(
        environ={"HFAGENT_IHEYE": "1"},
        platform_name="linux",
        control=control,
        host=_host(),
    )
    assert forced.reason == "not-macos"
    assert calls == []


def test_missing_pyobjc_does_not_spawn(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("hfagent.iheye.supervisor.pyobjc_available", lambda: False)
    monkeypatch.setattr(
        "hfagent.iheye.supervisor.subprocess.Popen",
        lambda *args, **kwargs: calls.append(1),
    )
    control = EyeSupervisor(
        socket_dir=tmp_path,
        position_path=tmp_path / "pos.json",
        log_path=tmp_path / "iheye.log",
        platform_name="darwin",
    )
    status = control.start(_host())
    assert status.running is False
    assert status.reason == "missing-pyobjc"
    assert INSTALL_HINT in status.message
    assert calls == []
    assert isinstance(pyobjc_available(), bool)


class _Proc:
    def __init__(self, alive=True):
        self.returncode = None if alive else 1
        self.signals = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired(cmd="iheye", timeout=timeout)
        return self.returncode

    def terminate(self):
        self.signals.append("term")
        self.returncode = 0

    def kill(self):
        self.signals.append("kill")
        self.returncode = -9

    pid = 4242


def test_supervisor_pushes_state_and_stops(monkeypatch, tmp_path):
    monkeypatch.setattr("hfagent.iheye.supervisor.pyobjc_available", lambda: True)
    proc = _Proc()
    spawned = {}

    def fake_popen(cmd, **kwargs):
        spawned["cmd"] = cmd
        spawned["kwargs"] = kwargs
        return proc

    monkeypatch.setattr("hfagent.iheye.supervisor.subprocess.Popen", fake_popen)
    parent = os.getpid()
    control = EyeSupervisor(
        socket_dir=tmp_path,
        position_path=tmp_path / "pos.json",
        log_path=tmp_path / "iheye.log",
        platform_name="darwin",
    )
    path = tmp_path / f"hfagent-iheye-{parent}.sock"
    got = []
    server = EyeServer(str(path), got.append)
    server.start()
    try:
        status = control.start(_host(), parent_pid=parent)
        assert status.running
        assert spawned["cmd"][1:3] == ["-m", "hfagent.iheye"]
        assert spawned["kwargs"]["start_new_session"] is True
        control.set_state("waiting")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not any(m.get("state") == "waiting" for m in got):
            time.sleep(0.02)
        assert any(m.get("state") == "waiting" for m in got)
        control.stop()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not any(m.get("cmd") == "quit" for m in got):
            time.sleep(0.02)
        assert any(m.get("cmd") == "quit" for m in got)
        assert "term" in proc.signals
    finally:
        control.stop()
        server.stop()


def test_supervisor_reports_a_helper_that_exits_immediately(monkeypatch, tmp_path):
    monkeypatch.setattr("hfagent.iheye.supervisor.pyobjc_available", lambda: True)

    def fake_popen(_cmd, **kwargs):
        fh = kwargs.get("stdout")
        if fh is not None:
            fh.write("boom\n")
            fh.flush()
        return _Proc(alive=False)

    monkeypatch.setattr("hfagent.iheye.supervisor.subprocess.Popen", fake_popen)
    control = EyeSupervisor(
        socket_dir=tmp_path,
        position_path=tmp_path / "pos.json",
        log_path=tmp_path / "iheye.log",
        platform_name="darwin",
    )
    status = control.start(_host(), parent_pid=os.getpid())
    assert status.reason == "helper-exited"
    assert "boom" in status.message


def test_publish_and_agent_turn(monkeypatch):
    states = []
    monkeypatch.setattr("hfagent.iheye.publish", lambda state: states.append(state))
    agent = Agent(Config(api_key="fake", base_url="http://127.0.0.1:9", model="m"), default_registry(), AgentUI())
    monkeypatch.setattr(agent, "_run", lambda text: "ok")
    assert agent.run("hi") == "ok"
    assert states == ["thinking", "done"]

    def boom(_text):
        raise RuntimeError("fail")

    monkeypatch.setattr(agent, "_run", boom)
    with pytest.raises(RuntimeError):
        agent.run("hi")
    assert states == ["thinking", "done", "thinking", "done"]


def test_approval_publishes_waiting(monkeypatch):
    states = []

    def record(state):
        states.append(state)

    class Tool:
        name = "run_command"
        needs_approval = True

        def preview(self, **_kwargs):
            return "echo hi"

    class UI(AgentUI):
        def approve(self, name, args, preview=None):
            return True

    agent = Agent(Config(approval="default"), default_registry(), UI())
    # _approved imports publish inside the method, so patch the package function.
    monkeypatch.setattr("hfagent.iheye.publish", record)
    assert agent._approved(Tool(), {"command": "echo hi"}) is True
    assert states == ["waiting", "thinking"]

    agent.config.approval = "auto"
    assert agent._approved(Tool(), {"command": "echo hi"}) is True
    assert states == ["waiting", "thinking"]


def test_iheye_command_persists_and_reports(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setattr("hfagent.config.user_env_path", lambda: env_path)
    monkeypatch.delenv("HFAGENT_IHEYE", raising=False)

    class UI(AgentUI):
        def __init__(self):
            self.status = []

        def on_status(self, text):
            self.status.append(text)

    agent = Agent(Config(), default_registry(), AgentUI())
    ui = UI()
    assert "/iheye" in HELP
    assert dispatch_slash(agent, ui, "/iheye") == "handled"
    assert "macOS" in ui.status[-1] or "iheye" in ui.status[-1]

    assert dispatch_slash(agent, ui, "/iheye off") == "handled"
    assert ui.status[-1] == "iheye off"
    assert os.environ["HFAGENT_IHEYE"] == "0"
    assert "HFAGENT_IHEYE=0" in env_path.read_text(encoding="utf-8")

    message = handle_command("on")
    assert "HFAGENT_IHEYE=1" in env_path.read_text(encoding="utf-8")
    assert os.environ["HFAGENT_IHEYE"] == "1"
    assert "macOS" in message or message == "iheye on"
    assert handle_command("sideways") == "usage: /iheye on|off"
    monkeypatch.setenv("HFAGENT_IHEYE", "0")
    assert "HFAGENT_IHEYE=0" in status_text()


def test_config_report_mentions_iheye(monkeypatch):
    monkeypatch.delenv("HFAGENT_IHEYE", raising=False)
    report = format_config_report(Config.load())
    assert "iheye=" in report
    monkeypatch.setenv("HFAGENT_IHEYE", "0")
    report = format_config_report(Config.load())
    assert "iheye=off" in report
    assert "process environment" in report


def test_publish_swallows_supervisor_errors(monkeypatch):
    def boom():
        raise RuntimeError("down")

    monkeypatch.setattr("hfagent.iheye.get_supervisor", boom)
    publish("thinking")
    publish("not-a-state")
