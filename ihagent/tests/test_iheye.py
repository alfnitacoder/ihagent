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


def test_helper_main_hints_when_qt_is_missing(monkeypatch, capsys):
    monkeypatch.setattr("hfagent.iheye.__main__.sys.platform", "linux")
    monkeypatch.setattr("hfagent.iheye.backend.qt_available", lambda: False)
    assert (
        helper_main(
            ["--parent-pid", "1", "--socket", "/tmp/iheye.sock", "--position", "/tmp/pos.json"]
        )
        == 0
    )
    assert "hfagent[iheye]" in capsys.readouterr().err


def test_start_is_a_noop_when_disabled_or_qt_is_missing(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("hfagent.iheye.backend.qt_available", lambda: False)
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
    assert forced.reason == "missing-pyside"
    assert "PySide6" in forced.message
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

    monkeypatch.setattr("hfagent.iheye.backend.qt_available", lambda: False)
    message = handle_command("on")
    assert "HFAGENT_IHEYE=1" in env_path.read_text(encoding="utf-8")
    assert os.environ["HFAGENT_IHEYE"] == "1"
    assert message == "iheye on" or "PySide6" in message or "PyObjC" in message or "macOS" in message
    assert handle_command("sideways") == "usage: /iheye on|off | track on|off | summary on|off | look on|off"
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


def test_turn_publishes_summary_without_changing_state_order(monkeypatch):
    states = []
    summaries = []
    monkeypatch.setattr("hfagent.iheye.publish", lambda state: states.append(state))
    monkeypatch.setattr("hfagent.iheye.publish_summary", lambda text: summaries.append(text))
    agent = Agent(Config(api_key="fake", base_url="http://127.0.0.1:9", model="m"), default_registry(), AgentUI())
    answer = "First line of the answer\nSecond line\nThird line\nFourth line\nFifth line"
    monkeypatch.setattr(agent, "_run", lambda text: answer)
    assert agent.run("hi") == answer
    assert states == ["thinking", "done"]
    assert summaries == [answer]


def test_reply_summary_and_card_policy():
    from hfagent.iheye.panels import (
        approval_answer,
        approval_body,
        clean_chat_text,
        drop_contents,
        layout_drop,
        should_restore_front,
        step_hover,
        step_summary_timer,
        summarize_answer,
        trim_reply,
    )

    assert clean_chat_text("  hello \r\n") == "hello"
    assert clean_chat_text("\x00") == ""
    short = summarize_answer("one\ntwo\nthree\nfour\nfive")
    assert short.startswith("one")
    assert "five" not in short.split("\n")[0]
    assert len(short.splitlines()) <= 4
    assert summarize_answer("   ") == ""
    latest = trim_reply("\n".join(f"line {i}" for i in range(1, 12)))
    assert "line 11" in latest
    assert "line 1\n" not in latest
    assert approval_answer("y") is True
    assert approval_answer("N") is False
    assert approval_answer("maybe") is None
    assert "run_command" in approval_body("run_command", "echo hi")
    assert drop_contents(hover=True, approval=True, summary=True) == "chat-approval"
    assert drop_contents(hover=False, approval=True, summary=True) == "approval"
    assert drop_contents(hover=False, approval=False, summary=True) == "summary"
    assert drop_contents(hover=False, approval=False, summary=False) == "closed"
    frames = layout_drop("chat-approval", 280, 196)
    assert frames["yes"].y < frames["field"].y < frames["reply"].y
    assert "yes" not in layout_drop("chat", 280, 156)
    assert set(layout_drop("summary", 280, 112)) == {"reply"}

    opened, away, suppressed = step_hover(
        open_now=False, inside=True, focused=False, away_for=0, suppressed=False, dt=0.1
    )
    assert opened and away == 0 and not suppressed
    still, away, suppressed = step_hover(
        open_now=True, inside=False, focused=True, away_for=0, suppressed=False, dt=1
    )
    assert still and away == 0
    waiting, away, _suppressed = step_hover(
        open_now=True, inside=False, focused=False, away_for=0.1, suppressed=False, dt=0.1, delay=0.35
    )
    assert waiting and away == pytest.approx(0.2)
    closed, away, _suppressed = step_hover(
        open_now=True, inside=False, focused=False, away_for=0.3, suppressed=False, dt=0.1, delay=0.35
    )
    assert not closed and away == 0
    shut, _away, suppressed = step_hover(
        open_now=True, inside=True, focused=True, away_for=0, suppressed=True, dt=0.1
    )
    assert not shut and suppressed
    left, _away, suppressed = step_hover(
        open_now=False, inside=False, focused=False, away_for=0, suppressed=True, dt=0.1
    )
    assert not left and not suppressed

    remaining, showing = step_summary_timer(8, False, 3, True)
    assert showing and remaining == 5
    paused, showing = step_summary_timer(2, True, 5, True)
    assert showing and paused == 2
    done, showing = step_summary_timer(0.2, False, 0.2, True)
    assert not showing and done == 0
    assert should_restore_front(7, 7, 4) is True
    assert should_restore_front(4, 7, 4) is False
    assert should_restore_front(7, 7, None) is False
    assert should_restore_front(7, 7, 7) is False


def test_gaze_follows_pointer_and_keys_are_not_read():
    from pathlib import Path

    from hfagent.iheye.appearance import eye_geometry, pupil_offset
    from hfagent.iheye.tracking import (
        KEYBOARD_HINT,
        gaze_from_points,
        hint_for_monitor,
        key_activity_handler,
        tick_interval,
        tracked_pupil,
        typing_reaction,
    )

    right, level = gaze_from_points(0, 0, 300, 0)
    assert right > 0
    assert abs(level) < 0.01
    up = gaze_from_points(10, 10, 10, 400)
    assert up[1] > 0
    assert gaze_from_points(5, 5, 5.2, 5.2) == (0.0, 0.0)
    base = pupil_offset("idle", 0.2)
    aimed = tracked_pupil(base, (4.0, 0.0))
    assert aimed[0] > base[0]
    quiet = eye_geometry("idle", 0.2)
    looking = eye_geometry("idle", 0.2, pupil_override=(3.0, 0.0))
    assert looking.pupil[0] > quiet.pupil[0]
    offset, blink = typing_reaction(0.05, 0.2)
    assert blink > 0.5
    assert typing_reaction(None, 1) == ((0.0, 0.0), 0.0)
    assert typing_reaction(3.0, 1)[1] == 0.0
    assert offset != (0.0, 0.0)
    assert tick_interval(
        visible=False, state="idle", phase=0, tracking=True,
        pointer_near_eye=False, mouse_moved=False, typing=False,
    ) == 0.5
    assert tick_interval(
        visible=True, state="idle", phase=1, tracking=True,
        pointer_near_eye=False, mouse_moved=False, typing=False,
    ) == pytest.approx(1 / 8)
    assert tick_interval(
        visible=True, state="thinking", phase=0, tracking=False,
        pointer_near_eye=False, mouse_moved=False, typing=False,
    ) == pytest.approx(1 / 30)
    assert tick_interval(
        visible=True, state="idle", phase=0, tracking=True,
        pointer_near_eye=False, mouse_moved=True, typing=False,
    ) == pytest.approx(1 / 30)
    assert hint_for_monitor(None) == KEYBOARD_HINT
    assert hint_for_monitor(object()) == ""
    seen = []

    class Event:
        def characters(self):
            seen.append("leak")

        def charactersIgnoringModifiers(self):
            seen.append("leak")

        keyCode = 4

    key_activity_handler(lambda: seen.append("tick"))(Event())
    assert seen == ["tick"]
    overlay = Path(__file__).resolve().parents[1] / "src" / "hfagent" / "iheye" / "overlay_app.py"
    source = overlay.read_text(encoding="utf-8")
    assert "key_activity_handler" in source
    assert "mouseLocation" in source
    assert "charactersIgnoringModifiers" not in source
    assert ".characters(" not in source


def test_track_and_summary_commands_persist(monkeypatch, tmp_path):
    from hfagent.iheye import get_supervisor, track_enabled, summary_enabled
    from hfagent.iheye.supervisor import flag_enabled

    env_path = tmp_path / ".env"
    monkeypatch.setattr("hfagent.config.user_env_path", lambda: env_path)
    monkeypatch.delenv("HFAGENT_IHEYE_TRACK", raising=False)
    monkeypatch.delenv("HFAGENT_IHEYE_SUMMARY", raising=False)
    assert flag_enabled("HFAGENT_IHEYE_TRACK", True, {}) is True
    assert flag_enabled("HFAGENT_IHEYE_TRACK", True, {"HFAGENT_IHEYE_TRACK": "off"}) is False
    assert flag_enabled("HFAGENT_IHEYE_SUMMARY", True, {"HFAGENT_IHEYE_SUMMARY": "weird"}) is True
    assert track_enabled({}) is True
    assert summary_enabled({"HFAGENT_IHEYE_SUMMARY": "0"}) is False

    assert handle_command("track") == "iheye track on"
    assert handle_command("track off") == "iheye track off"
    assert "HFAGENT_IHEYE_TRACK=0" in env_path.read_text(encoding="utf-8")
    assert os.environ["HFAGENT_IHEYE_TRACK"] == "0"
    assert handle_command("track") == "iheye track off"
    assert handle_command("summary off") == "iheye summary off"
    assert "HFAGENT_IHEYE_SUMMARY=0" in env_path.read_text(encoding="utf-8")
    assert handle_command("summary on") == "iheye summary on"
    assert handle_command("track maybe") == "usage: /iheye on|off | track on|off | summary on|off | look on|off"
    report = format_config_report(Config.load())
    assert "iheye_track=" in report
    assert "iheye_summary=" in report
    get_supervisor().note_options(track=True, summary=True)
    os.environ.pop("HFAGENT_IHEYE_TRACK", None)
    os.environ.pop("HFAGENT_IHEYE_SUMMARY", None)


def test_supervisor_delivers_chat_and_approval(monkeypatch, tmp_path):
    monkeypatch.setattr("hfagent.iheye.supervisor.pyobjc_available", lambda: True)
    proc = _Proc()
    spawned = {}

    def fake_popen(cmd, **kwargs):
        spawned["cmd"] = cmd
        return proc

    monkeypatch.setattr("hfagent.iheye.supervisor.subprocess.Popen", fake_popen)
    parent = os.getpid()
    control = EyeSupervisor(
        socket_dir=tmp_path,
        position_path=tmp_path / "pos.json",
        log_path=tmp_path / "iheye.log",
        platform_name="darwin",
    )
    chats = []
    answers = []
    control.on_chat = chats.append
    control.on_approval = answers.append
    path = tmp_path / f"hfagent-iheye-{parent}.sock"
    got = []
    server = EyeServer(str(path), got.append)
    server.start()
    try:
        status = control.start(_host(), parent_pid=parent)
        assert status.running
        assert "--track" in spawned["cmd"]
        assert "--summary" in spawned["cmd"]
        server.send({"cmd": "chat", "text": "  hello from the eye  "})
        server.send({"cmd": "approve", "yes": False})
        server.send({"cmd": "chat", "text": "   "})
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and (chats != ["hello from the eye"] or answers != [False]):
            time.sleep(0.02)
        assert chats == ["hello from the eye"]
        assert answers == [False]
        before = len(got)
        control.note_reply("alpha\nbeta\ngamma\ndelta\nepsilon\nzeta\neta", final=True)
        control.note_summary("Finished the task.\nDetails follow.\nMore.\nEven more.\nTrailing.")
        control.note_approval("run_command", "echo hi")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not (
            any("reply" in msg for msg in got)
            and any("summary" in msg for msg in got)
            and any(isinstance(msg.get("approval"), dict) for msg in got)
        ):
            time.sleep(0.02)
        replies = [msg["reply"] for msg in got[before:] if "reply" in msg]
        summaries = [msg["summary"] for msg in got[before:] if "summary" in msg]
        approvals = [msg["approval"] for msg in got[before:] if "approval" in msg]
        assert replies and "eta" in replies[-1]
        assert summaries and summaries[-1].startswith("Finished")
        assert len(summaries[-1].splitlines()) <= 4
        assert approvals[-1]["tool"] == "run_command"
        quiet = len(got)
        control.summary_enabled = False
        control.note_summary("should not send")
        time.sleep(0.15)
        assert len(got) == quiet
    finally:
        control.stop()
        server.stop()


def test_card_origin_prefers_below_the_eye():
    from hfagent.iheye.position import drop_origin

    below = drop_origin(100, 400, 88, 100, 280, 112, 0, 0, 1200, 800)
    assert below[1] < 400
    above = drop_origin(10, 8, 88, 100, 280, 112, 0, 0, 800, 600)
    assert above[1] >= 8 + 100
