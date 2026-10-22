"""ihmouse overlay with the GUI mocked. No display, no AppKit, no Qt."""

import sys

import pytest

from hfagent.agent import Agent, AgentUI
from hfagent.config import Config, format_config_report
from hfagent.desktop import reset_for_tests
from hfagent.desktop.geometry import Monitor, Rect
from hfagent.desktop.overlay_draw import BLACK, CYAN, WHITE, describe_scene
from hfagent.desktop.overlay_geom import (
    appkit_window_frame,
    label_box,
    monitor_views,
    monitors_from_appkit_frames,
    place_ops,
    pointer_to_view,
    qt_window_geometry,
    virtual_rect,
)
from hfagent.desktop.overlay_host import (
    NullOverlay,
    RecordingOverlay,
    decode_message,
    encode_message,
    get_overlay,
    overlay_requested,
    prepare_launch,
    reset_overlay,
    set_overlay,
)
from hfagent.desktop.overlay_motion import (
    FADE_SECONDS,
    IDLE_SECONDS,
    CursorMotion,
    OverlayShow,
    effect_for,
    make_cue,
    overlay_scale,
)
from hfagent.desktop.overlay_window import (
    apply_appkit_panel,
    apply_qt_overlay,
    backend_note,
    choose_overlay_level,
    windows_clickthrough_exstyle,
    windows_exclude_capture,
    WS_EX_LAYERED,
    WS_EX_NOACTIVATE,
    WS_EX_TRANSPARENT,
)
from hfagent.prompts import SYSTEM_PROMPT

from tests.test_mouse import Pointer, _controller, _enable, _snapshot


@pytest.fixture(autouse=True)
def _isolated_overlay(monkeypatch, tmp_path):
    monkeypatch.setenv("HFAGENT_MOUSE_LOG", str(tmp_path / "mouse.log"))
    monkeypatch.delenv("HFAGENT_MOUSE", raising=False)
    monkeypatch.delenv("HFAGENT_MOUSE_OVERLAY", raising=False)
    monkeypatch.delenv("HFAGENT_MOUSE_APPROVAL", raising=False)
    reset_for_tests()
    yield
    reset_for_tests()


class _OrderOverlay(RecordingOverlay):
    def __init__(self, order):
        super().__init__()
        self.order = order

    def propose(self, cue, origin=None):
        self.order.append("propose")
        super().propose(cue, origin)

    def act(self, effect):
        self.order.append(effect)
        super().act(effect)

    def wait_arrived(self, timeout=1.0):
        self.order.append("arrived")
        return True

    def wait_effect(self, timeout=0.16):
        self.order.append("beat")

    def release(self):
        self.order.append("release")
        super().release()

    def suspend(self):
        self.order.append("suspend")
        super().suspend()

    def resume(self):
        self.order.append("resume")
        super().resume()


def test_glide_is_smooth_and_idle_fade_waits_until_the_action_finishes():
    motion = CursorMotion(reduce_motion=False)
    motion.propose(make_cue("click", 100, 0, "Save", Rect(80, -16, 40, 32)), origin=(0, 0))
    assert motion.glide_duration > 0
    motion.tick(motion.glide_duration * 0.25)
    assert motion.x < 25
    motion.tick(motion.glide_duration)
    assert motion.arrived
    assert abs(motion.x - 100) < 0.01
    motion.tick(IDLE_SECONDS + FADE_SECONDS + 1)
    assert motion.frame().opacity == 1
    assert motion.frame().visible
    motion.release()
    motion.tick(IDLE_SECONDS)
    assert motion.frame().opacity == 1
    motion.tick(FADE_SECONDS)
    assert motion.frame().visible is False


def test_reduce_motion_snaps_and_hide_is_immediate():
    motion = CursorMotion(reduce_motion=True)
    motion.propose(make_cue("move", 50, 20, "Go"), origin=(1, 2))
    assert motion.arrived
    assert (motion.x, motion.y) == (50, 20)
    motion.hide()
    assert motion.tick(0.05).visible is False


def test_a_later_glide_starts_where_the_robot_already_is():
    motion = CursorMotion(reduce_motion=False)
    motion.propose(make_cue("click", 0, 0, "A"), origin=(0, 0))
    motion.propose(make_cue("click", 100, 0, "B"), origin=(999, 999))
    motion.tick(0)
    assert abs(motion.x) < 0.01


def test_robot_matches_iheye_colors_and_labels_the_target():
    motion = CursorMotion(reduce_motion=True)
    bounds = Rect(100, 200, 80, 40)
    motion.propose(make_cue("click", 140, 220, "Save", bounds), origin=(0, 0))
    ops = describe_scene(motion.frame())
    body = next(op for op in ops if op["part"] == "body" and op["op"] == "ellipse")
    face = next(op for op in ops if op["part"] == "face")
    eye = next(op for op in ops if op["part"] == "eye")
    ring = next(op for op in ops if op["part"] == "ring")
    label = next(op for op in ops if op["op"] == "text")
    highlight = next(op for op in ops if op["part"] == "highlight")
    assert body["fill"][:3] == WHITE[:3]
    assert face["fill"][:3] == BLACK[:3]
    assert eye["fill"][:3] == CYAN[:3]
    assert ring["stroke"][:3] == CYAN[:3]
    assert label["text"] == "Click: Save"
    assert (highlight["x"], highlight["y"], highlight["w"], highlight["h"]) == (100, 200, 80, 40)
    assert highlight["stroke"][:3] == CYAN[:3]
    motion.act("pulse")
    motion.tick(0.05)
    assert any(op["part"] == "pulse" for op in describe_scene(motion.frame()))
    motion.act("typing")
    motion.tick(0.05)
    typing = [op for op in describe_scene(motion.frame()) if op["part"] == "typing"]
    assert len(typing) >= 3
    assert effect_for("click") == "pulse"
    assert effect_for("type") == "typing"
    assert effect_for("move") == ""


def test_label_flips_below_the_target_at_the_top_of_the_display():
    above = label_box(Rect(100, 200, 80, 40), "Click: Save", Rect(0, 0, 1000, 800))
    below = label_box(Rect(10, 0, 80, 40), "Click: Save", Rect(0, 0, 1000, 800))
    assert above.y < 200
    assert below.y > 40


def test_virtual_desktop_keeps_negative_origins_and_ignores_hidpi_scale():
    monitors = [Monitor(0, 0, 1440, 900, 2.0), Monitor(-1920, 0, 1920, 1080, 1.5)]
    virtual = virtual_rect(monitors)
    assert virtual == Rect(-1920, 0, 3360, 1080)
    assert pointer_to_view(-1500, 100, virtual) == (420, 100)
    assert appkit_window_frame(virtual, 900)[0] == -1920
    assert qt_window_geometry(virtual)[:2] == (-1920, 0)
    stacked = virtual_rect([Monitor(0, 0, 1920, 1080, 2), Monitor(0, -1080, 1920, 1080, 2)])
    assert stacked == Rect(0, -1080, 1920, 2160)
    assert appkit_window_frame(stacked, 1080)[1] == 0
    assert pointer_to_view(100, -500, stacked) == (100, 580)
    found, height = monitors_from_appkit_frames(
        [(0, 0, 1920, 1080), (-1920, 0, 1920, 1080)],
        [2, 1],
    )
    assert height == 1080
    assert found[0].width == 1920
    assert found[0].scale == 2
    assert found[1].x == -1920
    highlight = {"part": "highlight", "op": "roundrect", "x": 100, "y": 40, "w": 10, "h": 10}
    placed = place_ops([highlight], Rect(-1920, 0, 3840, 1080))
    assert placed[0]["x"] == 2020


def test_windows_are_clickthrough_and_skip_screen_capture():
    class Panel:
        def __init__(self):
            self.calls = {}

        def setLevel_(self, value):
            self.calls["level"] = value

        def setCollectionBehavior_(self, value):
            self.calls["behavior"] = value

        def setFloatingPanel_(self, value):
            self.calls["floating"] = value

        def setHidesOnDeactivate_(self, value):
            self.calls["hides"] = value

        def setCanHide_(self, value):
            self.calls["canhide"] = value

        def setOpaque_(self, value):
            self.calls["opaque"] = value

        def setHasShadow_(self, value):
            self.calls["shadow"] = value

        def setBackgroundColor_(self, value):
            self.calls["bg"] = value

        def setIgnoresMouseEvents_(self, value):
            self.calls["ignores"] = value

        def setSharingType_(self, value):
            self.calls["sharing"] = value

        def setBecomesKeyOnlyIfNeeded_(self, value):
            self.calls["key"] = value

        def setWorksWhenModal_(self, value):
            self.calls["modal"] = value

    panel = Panel()
    apply_appkit_panel(panel, clear_color="clear", floating_level=3, sharing_none=0, behavior=7)
    assert panel.calls["ignores"] is True
    assert panel.calls["opaque"] is False
    assert panel.calls["sharing"] == 0
    assert panel.calls["hides"] is False
    assert panel.calls["level"] == 3

    class Flag:
        def __init__(self, value):
            self.value = value

        def __int__(self):
            return self.value

    class WindowType:
        FramelessWindowHint = Flag(1)
        WindowStaysOnTopHint = Flag(2)
        Tool = Flag(4)
        WindowDoesNotAcceptFocus = Flag(8)
        WindowTransparentForInput = Flag(16)

    class WidgetAttribute:
        WA_TranslucentBackground = "translucent"
        WA_ShowWithoutActivating = "noactivate"
        WA_TransparentForMouseEvents = "clickthrough"

    class FocusPolicy:
        NoFocus = "nofocus"

    Qt = type(
        "Qt",
        (),
        {
            "WindowType": WindowType,
            "WidgetAttribute": WidgetAttribute,
            "FocusPolicy": FocusPolicy,
        },
    )

    class Widget:
        def __init__(self):
            self.flags = 0
            self.attrs = []
            self.focus = None

        def setWindowFlags(self, flags):
            self.flags = int(flags)

        def setAttribute(self, name, value=True):
            self.attrs.append((name, value))

        def setFocusPolicy(self, policy):
            self.focus = policy

    widget = Widget()
    apply_qt_overlay(widget, Qt)
    assert widget.flags == 31
    assert ("clickthrough", True) in widget.attrs
    assert widget.focus == "nofocus"
    style = windows_clickthrough_exstyle(0x100)
    assert style & WS_EX_TRANSPARENT
    assert style & WS_EX_LAYERED
    assert style & WS_EX_NOACTIVATE
    assert style & 0x100
    assert windows_exclude_capture() == 0x11


def test_missing_gui_does_not_launch_a_process():
    launched = []
    proc, note = prepare_launch("PySide6 missing", lambda: launched.append(1))
    assert proc is None and "PySide6" in note and launched == []
    proc, note = prepare_launch("", lambda: "helper")
    assert proc == "helper" and note == ""
    assert "PyObjC" in backend_note("darwin", {}, lambda _name: False)
    assert backend_note("darwin", {}, lambda _name: True) == ""
    assert "PySide6" in backend_note("win32", {}, lambda _name: False)
    assert "DISPLAY" in backend_note("linux", {}, lambda _name: True)
    assert backend_note("linux", {"DISPLAY": ":0"}, lambda _name: True) == ""
    assert backend_note("linux", {"WAYLAND_DISPLAY": "wayland-0"}, lambda _name: True) == ""
    reset_overlay()
    assert isinstance(get_overlay(), NullOverlay)


def test_protocol_reports_arrival_without_a_window():
    show = OverlayShow(CursorMotion(reduce_motion=True))
    events = show.handle(
        {
            "op": "propose",
            "seq": 3,
            "x": 10,
            "y": 4,
            "label": "Click: Save",
            "action": "click",
            "ox": 0,
            "oy": 0,
            "bounds": [1, 2, 3, 4],
        }
    )
    assert events == [{"event": "arrived", "seq": 3}]
    assert show.motion.frame().label == "Click: Save"
    assert show.handle({"op": "suspend", "seq": 9}) == [{"event": "suspended", "seq": 9}]
    assert show.motion.frame().visible is False
    show.handle({"op": "resume"})
    assert show.motion.frame().visible is True
    show.handle({"op": "hide"})
    assert show.motion.visible is False
    delayed = OverlayShow(CursorMotion(reduce_motion=False))
    assert delayed.handle(
        {"op": "propose", "seq": 1, "x": 400, "y": 0, "label": "Click: Go", "ox": 0, "oy": 0}
    ) == []
    seen = []
    for _ in range(40):
        _frame, extra = delayed.tick(0.05)
        seen.extend(extra)
    assert {"event": "arrived", "seq": 1} in seen
    assert decode_message(encode_message({"op": "hide"})) == {"op": "hide"}
    assert decode_message("not-json") is None


def test_click_glides_and_pulses_before_the_system_pointer(monkeypatch):
    _enable(monkeypatch)
    order = []
    overlay = _OrderOverlay(order)
    pointer = Pointer()
    pointer.click = lambda *args: order.append("click") or pointer.clicks.append(args)
    control, calls = _controller(pointer, lambda: _snapshot())
    control._overlay_client = overlay
    result = control.perform("click", element=1)
    assert "clicked" in result
    assert order == ["propose", "arrived", "pulse", "beat", "click", "release"]
    cue = overlay.events[0][1]
    assert cue.label == "Click: Save"
    assert cue.bounds == Rect(100, 200, 80, 40)
    assert overlay.events[0][2] == (100.0, 100.0)
    assert calls["grab"] == 0
    assert pointer.clicks


def test_declining_a_vision_target_does_not_screenshot(monkeypatch):
    _enable(monkeypatch)
    overlay = RecordingOverlay()
    pointer = Pointer()
    control, calls = _controller(pointer, lambda: _snapshot(elements=[]))
    control._overlay_client = overlay
    from hfagent.tools import default_registry

    class UI(AgentUI):
        def approve(self, name, args, preview=None):
            return False

    agent = Agent(Config(approval="auto"), default_registry(), UI())
    result = agent._execute(
        {"function": {"name": "mouse_click", "arguments": '{"target": "Save"}'}}
    )
    assert "declined" in result
    assert pointer.clicks == []
    assert calls["grab"] == 0
    assert not any(event[0] == "propose" for event in overlay.events)


def test_known_element_is_highlighted_during_approval_and_hidden_on_decline(monkeypatch):
    _enable(monkeypatch)
    overlay = RecordingOverlay()
    pointer = Pointer()
    control, calls = _controller(pointer, lambda: _snapshot())
    control._overlay_client = overlay
    from hfagent.tools import default_registry

    class UI(AgentUI):
        def approve(self, name, args, preview=None):
            return False

    agent = Agent(Config(approval="auto"), default_registry(), UI())
    result = agent._execute(
        {"function": {"name": "mouse_click", "arguments": '{"element": 1}'}}
    )
    assert "declined" in result
    assert pointer.clicks == []
    assert calls["grab"] == 0
    assert overlay.events[0][0] == "propose"
    assert overlay.events[0][1].label == "Click: Save"
    assert overlay.events[-1] == ("hide",)


def test_typing_indicator_precedes_the_real_keystrokes(monkeypatch):
    _enable(monkeypatch)
    order = []
    overlay = _OrderOverlay(order)
    pointer = Pointer()
    pointer.type_text = lambda text: order.append("type") or pointer.typed.append(text)
    control, _calls = _controller(pointer, lambda: _snapshot())
    control._overlay_client = overlay
    result = control.perform("type", element=1, text="hello")
    assert "typed" in result
    assert order.index("typing") < order.index("type")
    assert overlay.events[0][1].label == "Type: hello"


def test_vision_hides_the_robot_while_grabbing_then_pulses(monkeypatch):
    _enable(monkeypatch)
    order = []
    overlay = _OrderOverlay(order)
    pointer = Pointer()
    pointer.click = lambda *args: order.append("click") or pointer.clicks.append(args)

    def grab(rect):
        order.append("grab")
        return b"png-bytes", 200, 100

    control, _calls = _controller(
        pointer,
        lambda: _snapshot(elements=[], window=Rect(0, 0, 200, 100), error=""),
        grabber=grab,
        vision=lambda settings, messages: '{"found": true, "x": 100, "y": 50}',
    )
    control._overlay_client = overlay
    result = control.perform("click", target="Save")
    assert "vision" in result
    assert order.index("suspend") < order.index("grab") < order.index("resume")
    assert order.index("resume") < order.index("propose")
    assert order.index("pulse") < order.index("click")


def test_overlay_off_still_moves_the_system_pointer(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("HFAGENT_MOUSE_OVERLAY", "0")
    overlay = RecordingOverlay()
    pointer = Pointer()
    control, _calls = _controller(pointer, lambda: _snapshot())
    control._overlay_client = overlay
    assert "clicked" in control.perform("click", element=1)
    assert overlay.events == []
    assert pointer.clicks
    assert overlay_requested() is False


def test_password_target_is_not_highlighted(monkeypatch):
    _enable(monkeypatch)
    from tests.test_mouse import _password

    overlay = RecordingOverlay()
    pointer = Pointer()
    control, _calls = _controller(pointer, lambda: _snapshot(elements=[_password()], focused=_password()))
    control._overlay_client = overlay
    result = control.perform("type", element=2, text="secret")
    assert "password" in result
    assert pointer.clicks == []
    assert pointer.typed == []
    assert overlay.events == [] or all(event[0] == "hide" for event in overlay.events)


def test_slash_overlay_persists_and_hides(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setattr("hfagent.config.user_env_path", lambda: env_path)
    from hfagent.desktop.command import handle_command, status_text

    overlay = RecordingOverlay()
    set_overlay(overlay)
    monkeypatch.setenv("HFAGENT_MOUSE", "1")
    assert "overlay on" in status_text()
    assert handle_command("overlay off") == (
        "mouse overlay off. The system pointer still moves when you confirm an action."
    )
    assert "HFAGENT_MOUSE_OVERLAY=0" in env_path.read_text(encoding="utf-8")
    assert ("hide",) in overlay.events
    assert "overlay off" in status_text()
    assert handle_command("overlay on").startswith("mouse overlay on")
    assert "HFAGENT_MOUSE_OVERLAY=1" in env_path.read_text(encoding="utf-8")
    handle_command("stop")
    assert ("hide",) in overlay.events
    report = format_config_report(Config.load())
    assert "mouse_overlay=" in report
    assert "ihmouse" in SYSTEM_PROMPT


def test_importing_the_overlay_does_not_open_a_toolkit():
    import inspect

    import hfagent.desktop.overlay_draw
    import hfagent.desktop.overlay_main
    import hfagent.desktop.overlay_motion

    assert hfagent.desktop.overlay_main.main
    assert "AppKit" not in sys.modules
    assert "PySide6" not in sys.modules
    assert "pyautogui" not in sys.modules
    source = inspect.getsource(hfagent.desktop.overlay_main.run_appkit)
    assert source.index("sharedApplication") < source.index("start_reader")
    assert source.index("start_reader") < source.index("runEventLoop")
    assert "setNeedsDisplay_" in source
    assert "setWantsLayer_" in source
    assert "isOpaque" in source
    assert "choose_overlay_level" in source
    steps = hfagent.desktop.overlay_main.APPKIT_STARTUP_STEPS
    assert steps.index("stdin_thread") > steps.index("shared_application")
    assert steps.index("panels_per_screen") > steps.index("shared_application")


def test_overlay_level_prefers_assistive_over_floating():
    assert choose_overlay_level(floating=3, status=25, assistive=1500) == 1500
    assert choose_overlay_level(floating=3, status=25, assistive=None) == 25
    assert choose_overlay_level(floating=3, status=25, assistive=0) == 25
    assert choose_overlay_level(floating=3, status=3, assistive=None) == 3


def test_each_display_gets_its_own_view_including_retina():
    views = monitor_views(
        [Monitor(0, 0, 1440, 900, 2.0), Monitor(-1920, 0, 1920, 1080, 1.0)]
    )
    assert views == [Rect(0, 0, 1440, 900), Rect(-1920, 0, 1920, 1080)]
    arrow = {"op": "polygon", "points": [(10.0, 20.0), (0.0, 0.0), (4.0, 8.0)], "part": "arrow"}
    placed = place_ops([arrow], views[1])
    assert placed[0]["points"][0] == (1930.0, 20.0)


def test_arrow_is_distinct_and_clicks_do_not_share_one_ripple():
    motion = CursorMotion(reduce_motion=True)
    motion.propose(make_cue("click", 140, 220, "Save", Rect(100, 200, 80, 40)), origin=(0, 0))
    ops = describe_scene(motion.frame())
    arrow = next(op for op in ops if op["part"] == "arrow")
    assert arrow["op"] == "polygon"
    assert arrow["fill"][:3] == WHITE[:3]
    assert arrow["stroke"][:3] == CYAN[:3]
    assert any(op.get("text") == "IH" for op in ops)
    assert effect_for("right_click") == "right"
    assert effect_for("double_click") == "double"
    assert effect_for("click") == "pulse"
    motion.act("right")
    motion.tick(0.05)
    assert any(op["part"] == "right" for op in describe_scene(motion.frame()))
    motion.act("double")
    motion.tick(0.2)
    doubles = [op for op in describe_scene(motion.frame()) if op["part"] == "double"]
    assert len(doubles) >= 2
    motion.show_label = False
    assert not any(op.get("text") == "Click: Save" for op in describe_scene(motion.frame()))
    assert any(op.get("text") == "IH" for op in describe_scene(motion.frame()))


def test_glide_leaves_a_trail_and_sticky_does_not_fade():
    motion = CursorMotion(reduce_motion=False)
    motion.propose(make_cue("move", 400, 0, "Go"), origin=(0, 0))
    for _ in range(6):
        motion.tick(motion.glide_duration / 6.0)
    assert len(motion.frame().trail) >= 2
    assert any(op["part"] == "trail" for op in describe_scene(motion.frame()))
    motion.sticky = True
    motion.release()
    motion.tick(IDLE_SECONDS + FADE_SECONDS + 1)
    assert motion.frame().visible is True
    assert motion.frame().opacity == 1


def test_mouse_on_presents_the_arrow_and_scale_is_clamped(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setattr("hfagent.config.user_env_path", lambda: env_path)
    from hfagent.desktop.command import handle_command

    overlay = RecordingOverlay()
    set_overlay(overlay)
    assert handle_command("on").startswith("mouse on")
    assert overlay.events[0][0] == "present"
    assert handle_command("overlay scale 1.4") == "mouse overlay scale 1.4"
    assert "HFAGENT_MOUSE_OVERLAY_SCALE=1.4" in env_path.read_text(encoding="utf-8")
    assert overlay.events[-1][0] == "restyle"
    assert handle_command("overlay label off") == "mouse overlay label off"
    assert handle_command("overlay scale 9") == "usage: /mouse overlay scale 0.6–2.5"
    assert overlay_scale() == 1.4
    motion = CursorMotion(reduce_motion=True)
    motion.scale = 2
    motion.propose(make_cue("show", 10, 10), origin=(10, 10))
    ops = describe_scene(motion.frame())
    badge = next(op for op in ops if op["part"] == "body")
    assert abs(badge["cx"] - (10 + 38 * 2)) < 0.01
    assert not any(op["part"] == "highlight" for op in ops)
