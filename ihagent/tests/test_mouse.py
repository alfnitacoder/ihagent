"""Desktop mouse control with mocked OS layers. No display, no pyautogui."""

import json
import sys

import pytest

from hfagent.agent import Agent, AgentUI
from hfagent.config import Config, format_config_report
from hfagent.desktop import begin_task, reset_for_tests, set_controller
from hfagent.desktop.controller import DesktopController
from hfagent.desktop.elements import (
    UiElement,
    TreeSnapshot,
    elements_from_raw,
    format_elements,
    is_password,
    normalize_role,
    walk_nodes,
)
from hfagent.desktop.geometry import (
    CaptureRegion,
    Monitor,
    Rect,
    appkit_frame_to_monitor,
    is_corner,
    pixels_to_pointer,
    scale_rect,
)
from hfagent.desktop.hotkey import chord_down, parse_hotkey
from hfagent.desktop.pointer import normalize_keys
from hfagent.desktop.screens import ensure_dpi_aware, monitors_from_xrandr
from hfagent.desktop.session import get_session
from hfagent.desktop.targeting import resolve_aim
from hfagent.desktop.vision import (
    build_image_messages,
    model_can_see,
    parse_point,
    resolve_vision,
    vision_setup_message,
)
from hfagent.prompts import SYSTEM_PROMPT
from hfagent.tools import default_registry


class Pointer:
    def __init__(self, pos=(100.0, 100.0)):
        self.pos = pos
        self.clicks = []
        self.moves = []
        self.typed = []
        self.keys = []
        self.scrolls = []

    def position(self):
        return self.pos

    def move(self, x, y):
        self.pos = (x, y)
        self.moves.append((x, y))

    def click(self, x, y, button="left", clicks=1):
        self.pos = (x, y)
        self.clicks.append((x, y, button, clicks))

    def scroll(self, x, y, amount):
        self.scrolls.append((x, y, amount))

    def type_text(self, text):
        self.typed.append(text)

    def press(self, keys):
        self.keys.append(list(keys))


def _save():
    return UiElement(1, "button", "Save", 100, 200, 80, 40, password=False)


def _password():
    return UiElement(2, "securetextfield", "Password", 100, 260, 180, 24, password=True)


def _snapshot(elements=None, focused=None, window=None, error="", app="Notes"):
    return TreeSnapshot(
        app=app,
        elements=list(elements if elements is not None else [_save()]),
        window=window if window is not None else Rect(0, 0, 800, 600),
        focused=focused,
        error=error,
    )


@pytest.fixture(autouse=True)
def _isolated_mouse(monkeypatch, tmp_path):
    monkeypatch.setenv("HFAGENT_MOUSE_LOG", str(tmp_path / "mouse.log"))
    monkeypatch.delenv("HFAGENT_MOUSE", raising=False)
    monkeypatch.delenv("HFAGENT_MOUSE_APPROVAL", raising=False)
    monkeypatch.delenv("HFAGENT_VISION_MODEL", raising=False)
    monkeypatch.delenv("HFAGENT_VISION_BASE_URL", raising=False)
    monkeypatch.delenv("HFAGENT_VISION_API_KEY", raising=False)
    monkeypatch.delenv("HFAGENT_MOUSE_OVERLAY", raising=False)
    reset_for_tests()
    yield
    reset_for_tests()


def _controller(pointer, reader, grabber=None, vision=None, monitors=None):
    calls = {"grab": 0, "vision": []}

    def grab(rect):
        calls["grab"] += 1
        if grabber is not None:
            return grabber(rect)
        return b"png-bytes", int(rect.width * 2), int(rect.height * 2)

    def see(settings, messages):
        calls["vision"].append((settings, messages))
        if vision is not None:
            return vision(settings, messages)
        return json.dumps({"found": True, "x": 100, "y": 50})

    control = DesktopController(
        tree_reader=reader,
        pointer=pointer,
        grabber=grab,
        vision=see,
        monitors=monitors or (lambda: [Monitor(0, 0, 1920, 1080, 1), Monitor(-1920, 0, 1920, 1080, 1)]),
        session=get_session(),
    )
    set_controller(control)
    return control, calls


def _enable(monkeypatch, approval="per-action", model="qwen2.5vl"):
    monkeypatch.setenv("HFAGENT_MOUSE", "1")
    monkeypatch.setenv("HFAGENT_MOUSE_APPROVAL", approval)
    if model:
        monkeypatch.setenv("HFAGENT_VISION_MODEL", model)
        monkeypatch.setenv("HFAGENT_VISION_BASE_URL", "http://127.0.0.1:11434/v1")


# ---------------------------------------------------------------- geometry


def test_retina_screenshot_maps_centre_to_window_centre():
    region = CaptureRegion(100, 80, 400, 300, 800, 600)
    assert pixels_to_pointer(region, 400, 300) == (300, 230)


def test_hidpi_one_and_a_half_scale_uses_measured_ratio():
    region = CaptureRegion(10, 20, 200, 100, 300, 150)
    assert pixels_to_pointer(region, 150, 75) == (110, 70)


def test_left_monitor_keeps_negative_origin():
    region = CaptureRegion(-1500, 100, 200, 100, 400, 200)
    assert pixels_to_pointer(region, 100, 50) == (-1450, 125)


def test_pixels_outside_the_image_are_rejected():
    region = CaptureRegion(0, 0, 100, 100, 200, 200)
    with pytest.raises(ValueError):
        pixels_to_pointer(region, 201, 10)


def test_scale_rect_divides_physical_pixels():
    scaled = scale_rect(Rect(200, 100, 800, 600), 2)
    assert (scaled.x, scaled.y, scaled.width, scaled.height) == (100, 50, 400, 300)
    assert scale_rect(Rect(1, 2, 3, 4), 1) == Rect(1, 2, 3, 4)


def test_appkit_frames_flip_to_top_left():
    primary = appkit_frame_to_monitor(0, 0, 1440, 900, 900, 2)
    assert (primary.x, primary.y, primary.width, primary.height, primary.scale) == (0, 0, 1440, 900, 2)
    left = appkit_frame_to_monitor(-1920, 0, 1920, 900, 900, 1)
    assert (left.x, left.y) == (-1920, 0)


def test_corners_include_left_display_and_pyautogui_origin():
    monitors = [Monitor(0, 0, 1440, 900), Monitor(-1920, 0, 1920, 1080)]
    assert is_corner(0, 0, monitors)
    assert is_corner(-1920, 0, monitors)
    assert is_corner(-1916, 3, monitors)
    assert not is_corner(100, 100, monitors)
    assert is_corner(0, 0, [])


def test_xrandr_parses_millimetre_form_and_negative_origin():
    text = "\n".join(
        [
            "Monitors: 2",
            " 0: +*eDP-1 1920/340x1080/190+0+0  eDP-1",
            " 1: +HDMI-1 2560/600x1440/340-1920+0  HDMI-1",
        ]
    )
    found = monitors_from_xrandr(text)
    assert [(m.x, m.y, m.width, m.height) for m in found] == [
        (0, 0, 1920, 1080),
        (-1920, 0, 2560, 1440),
    ]


def test_dpi_awareness_is_unavailable_off_windows():
    assert ensure_dpi_aware() == "unavailable"


# ---------------------------------------------------------------- elements


def test_numbered_list_and_password_normalisation():
    rows = [
        {"role": "AXWindow", "name": "Notes", "x": 0, "y": 0, "width": 800, "height": 600},
        {"role": "AXButton", "name": "Save", "x": 100, "y": 200, "width": 80, "height": 40},
        {"role": "AXStaticText", "name": "", "x": 1, "y": 1, "width": 10, "height": 10},
        {
            "role": "AXTextField",
            "name": "Password",
            "x": 10,
            "y": 10,
            "width": 40,
            "height": 16,
        },
        {"role": "Button", "name": "Change password", "x": 10, "y": 40, "width": 80, "height": 20},
    ]
    elements = elements_from_raw(rows)
    assert [element.role for element in elements] == ["button", "textfield", "button"]
    assert elements[1].password is True
    assert elements[2].password is False
    text = format_elements(elements, "Notes")
    assert '1. button "Save" bounds=(100,200,80,40)' in text
    assert "password" in text
    assert normalize_role("AXSecureTextField") == "securetextfield"
    assert is_password("securetextfield", "", False)


def test_walk_nodes_respects_the_limit():
    tree = {"id": "root", "kids": [{"id": str(i), "kids": []} for i in range(10)]}

    def children(node):
        return node["kids"]

    def attrs(node):
        return {"role": "button", "name": node["id"], "x": 0, "y": 0, "width": 1, "height": 1}

    rows = walk_nodes(tree, children, attrs, limit=3)
    assert len(rows) == 3


# ---------------------------------------------------------------- targeting


def test_click_by_id_uses_centre_and_does_not_screenshot(monkeypatch):
    _enable(monkeypatch)
    pointer = Pointer()
    control, calls = _controller(pointer, lambda: _snapshot())
    result = control.perform("click", element=1)
    assert pointer.clicks == [(140, 220, "left", 1)]
    assert "via accessibility" in result
    assert "button \"Save\"" in result
    assert calls["grab"] == 0
    assert calls["vision"] == []
    assert result.startswith("[mouse]")


def test_empty_tree_falls_back_to_vision_with_scale(monkeypatch):
    _enable(monkeypatch)
    pointer = Pointer()
    window = Rect(-1500, 100, 200, 100)

    def grab(rect):
        assert rect == window
        return b"png-bytes", 400, 200

    control, calls = _controller(
        pointer,
        lambda: _snapshot(elements=[], window=window),
        grabber=grab,
        vision=lambda settings, messages: '{"found": true, "x": 100, "y": 50}',
    )
    result = control.perform("click", target="Save")
    assert pointer.clicks == [(-1450, 125, "left", 1)]
    assert "via vision" in result
    assert calls["grab"] == 1
    content = calls["vision"][0][1][0]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_fractional_vision_coordinates_are_pixels_of_the_image(monkeypatch):
    _enable(monkeypatch)
    pointer = Pointer()
    window = Rect(-1500, 100, 200, 100)
    control, _calls = _controller(
        pointer,
        lambda: _snapshot(elements=[], window=window),
        grabber=lambda rect: (b"png-bytes", 400, 200),
        vision=lambda settings, messages: '{"found": true, "x": 0.25, "y": 0.25}',
    )
    control.perform("double_click", target="the save button")
    assert pointer.clicks == [(-1450, 125, "left", 2)]


def test_named_match_wins_over_vision(monkeypatch):
    _enable(monkeypatch)
    pointer = Pointer()
    control, calls = _controller(pointer, lambda: _snapshot())
    control.perform("right_click", target="save")
    assert pointer.clicks == [(140, 220, "right", 1)]
    assert calls["vision"] == []


def test_ambiguous_name_asks_for_an_id(monkeypatch):
    _enable(monkeypatch)
    draft = UiElement(1, "button", "Save draft", 100, 200, 80, 40)
    other = UiElement(2, "button", "Save copy", 300, 200, 80, 40)
    pointer = Pointer()
    control, calls = _controller(pointer, lambda: _snapshot(elements=[draft, other]))
    result = control.perform("click", target="Save")
    assert result.startswith("Error:")
    assert "more than one" in result
    assert pointer.clicks == []
    assert calls["vision"] == []


def test_explicit_coordinates_skip_the_tree_match(monkeypatch):
    _enable(monkeypatch)
    pointer = Pointer()
    control, calls = _controller(pointer, lambda: _snapshot(elements=[]))
    result = control.perform("move", x=400, y=250)
    assert pointer.moves == [(400, 250)]
    assert "via coordinates" in result
    assert calls["grab"] == 0


def test_vision_setup_when_the_chat_model_cannot_see(monkeypatch):
    monkeypatch.setenv("HFAGENT_MOUSE", "1")
    from hfagent.desktop.vision import bind_chat

    bind_chat(lambda: ("https://ollama.com/v1", "gpt-oss:120b", "secret-key"))
    pointer = Pointer()
    control, calls = _controller(pointer, lambda: _snapshot(elements=[]))
    result = control.perform("click", target="Save")
    assert "HFAGENT_VISION_MODEL" in result
    assert "qwen2.5vl" in result
    assert "secret-key" not in result
    assert pointer.clicks == []
    assert calls["vision"] == []
    assert not model_can_see("gpt-oss:120b")
    assert model_can_see("qwen2.5vl")
    assert model_can_see("gemma3")
    assert model_can_see("llama3.2-vision")


def test_parse_point_rejects_a_miss_and_keeps_integer_pixels():
    assert parse_point('{"found": false, "reason": "no button"}', 400, 200).startswith("Error:")
    assert parse_point('```json\n{"found": true, "x": 12, "y": 8}\n```', 400, 200) == (12, 8)
    assert parse_point('{"found": true, "x": 1, "y": 1}', 800, 600) == (1, 1)
    outside = parse_point('{"x": 900, "y": 1}', 800, 600)
    assert isinstance(outside, str) and outside.startswith("Error:")


def test_vision_settings_default_to_the_current_backend():
    settings = resolve_vision("https://ollama.com/v1", "qwen2.5vl", "abc", env={})
    assert settings.model == "qwen2.5vl"
    assert settings.base_url == "https://ollama.com/v1"
    assert settings.api_key == "abc"
    assert settings.model_source == "current model"
    overridden = resolve_vision(
        "https://ollama.com/v1",
        "gpt-oss:120b",
        "abc",
        env={
            "HFAGENT_VISION_MODEL": "gemma3",
            "HFAGENT_VISION_BASE_URL": "http://127.0.0.1:11434/v1",
            "HFAGENT_VISION_API_KEY": "vision-key",
        },
    )
    assert overridden.model == "gemma3"
    assert overridden.base_url == "http://127.0.0.1:11434/v1"
    assert overridden.api_key == "vision-key"
    assert "image_url" in json.dumps(build_image_messages("look", b"png"))


def test_resolve_aim_requests_vision_when_nothing_matches():
    resolved = resolve_aim([_save()], element=None, target="Cancel", x=None, y=None)
    assert resolved.description == "Cancel"


# ---------------------------------------------------------------- safety


def test_actions_do_nothing_until_mouse_is_on():
    pointer = Pointer()
    control, calls = _controller(pointer, lambda: _snapshot())
    result = control.perform("click", element=1)
    assert "mouse control is off" in result
    assert "/mouse on" in result
    assert pointer.clicks == []
    assert calls["grab"] == 0


def test_password_fields_are_not_typed_into(monkeypatch):
    _enable(monkeypatch)
    pointer = Pointer()
    field = _password()
    control, _calls = _controller(
        pointer,
        lambda: _snapshot(elements=[_save(), field], focused=field),
    )
    refused = control.perform("type", text="hunter2", element=2)
    assert refused.startswith("Error:")
    assert "password" in refused.lower()
    assert pointer.typed == []
    assert pointer.clicks == []
    focused = control.perform("type", text="hunter2")
    assert "password" in focused.lower()
    assert pointer.typed == []
    allowed = control.perform("key", key="escape")
    assert "pressed escape" in allowed
    assert pointer.keys == [["esc"]]
    denied = control.perform("key", key="a")
    assert denied.startswith("Error:")
    assert pointer.keys == [["esc"]]


def test_typing_a_named_field_ignores_a_stale_password_focus(monkeypatch):
    _enable(monkeypatch)
    pointer = Pointer()
    field = _password()
    control, _calls = _controller(
        pointer,
        lambda: _snapshot(elements=[_save(), field], focused=field),
    )
    result = control.perform("type", text="hello", element=1)
    assert "typed 'hello'" in result
    assert pointer.typed == ["hello"]
    assert pointer.clicks == [(140, 220, "left", 1)]


def test_corner_and_stop_block_the_next_action(monkeypatch):
    _enable(monkeypatch)
    pointer = Pointer(pos=(0.0, 0.0))
    control, _calls = _controller(pointer, lambda: _snapshot())
    stopped = control.perform("click", element=1)
    assert "screen corner" in stopped
    assert pointer.clicks == []
    pointer.pos = (100.0, 100.0)
    again = control.perform("click", element=1)
    assert "emergency stop is on" in again
    assert pointer.clicks == []


def test_moving_into_a_corner_is_refused(monkeypatch):
    _enable(monkeypatch)
    pointer = Pointer()
    control, _calls = _controller(pointer, lambda: _snapshot())
    result = control.perform("move", x=0, y=0)
    assert "screen corner" in result
    assert pointer.moves == []
    assert get_session().stopped


def test_per_task_approval_asks_once_then_a_new_turn_asks_again(monkeypatch):
    _enable(monkeypatch, approval="per-task")
    _controller(Pointer(), lambda: _snapshot())
    click = default_registry().get("mouse_click")
    listing = default_registry().get("mouse_elements")
    assert listing.requires_approval() is False
    assert click.requires_approval(element=1) is True
    click.run(element=1)
    assert click.requires_approval(element=1) is False
    begin_task()
    assert click.requires_approval(element=1) is True


def test_mouse_still_confirms_when_global_approval_is_auto(monkeypatch):
    _enable(monkeypatch)
    _controller(Pointer(), lambda: _snapshot())
    asked = []

    class UI(AgentUI):
        def approve(self, name, args, preview=None):
            asked.append((name, preview))
            return True

    agent = Agent(Config(approval="auto"), default_registry(), UI())
    tool = agent.registry.get("mouse_click")
    assert tool.force_approval is True
    assert agent._needs_approval(tool, {"element": 1}) is True
    assert agent._approved(tool, {"element": 1}) is True
    assert asked and asked[0][0] == "mouse_click"
    agent.config.approval = "auto"
    plain = type("Quiet", (), {"needs_approval": True, "force_approval": False, "preview": lambda self, **kw: None})()
    assert agent._approved(plain, {}) is True
    assert len(asked) == 1


def test_scroll_and_log_file(monkeypatch, tmp_path):
    _enable(monkeypatch)
    pointer = Pointer()
    control, _calls = _controller(pointer, lambda: _snapshot())
    result = control.perform("scroll", amount=-2)
    assert pointer.scrolls == [(100.0, 100.0, -2)]
    assert "scrolled -2" in result
    log = (tmp_path / "mouse.log").read_text(encoding="utf-8")
    assert "scrolled -2" in log
    assert get_session().recent(5)[-1] == log.strip().splitlines()[-1]


def test_describe_sends_one_image_and_does_not_click(monkeypatch):
    _enable(monkeypatch)
    pointer = Pointer()
    control, calls = _controller(
        pointer,
        lambda: _snapshot(),
        vision=lambda settings, messages: "A notes window with a Save button.",
    )
    result = control.perform("describe", question="What is on screen?")
    assert "Save button" in result
    assert pointer.clicks == []
    assert calls["grab"] == 1
    assert calls["vision"][0][0].model == "qwen2.5vl"


def test_missing_accessibility_is_explained_and_vision_still_runs(monkeypatch):
    _enable(monkeypatch)
    pointer = Pointer()
    window = Rect(0, 0, 200, 100)
    control, _calls = _controller(
        pointer,
        lambda: _snapshot(
            elements=[],
            window=window,
            error="Error: macOS Accessibility permission is off.",
        ),
        grabber=lambda rect: (b"png-bytes", 200, 100),
        vision=lambda settings, messages: '{"found": true, "x": 100, "y": 50}',
    )
    listed = control.perform("elements")
    assert "Accessibility permission" in listed
    clicked = control.perform("click", target="Save")
    assert "Accessibility permission" in clicked or "via vision" in clicked
    assert pointer.clicks


def test_platform_readers_explain_missing_libraries(monkeypatch):
    monkeypatch.setitem(sys.modules, "ApplicationServices", None)
    monkeypatch.setitem(sys.modules, "pywinauto", None)
    monkeypatch.setitem(sys.modules, "uiautomation", None)
    monkeypatch.setitem(sys.modules, "pyatspi", None)
    from hfagent.desktop.atspi_linux import read_tree as read_linux
    from hfagent.desktop.ax_macos import read_tree as read_mac
    from hfagent.desktop.uia_windows import read_tree as read_windows

    assert "PyObjC" in read_mac().error
    assert "pywinauto" in read_windows().error
    assert "AT-SPI" in read_linux().error


def test_importing_tools_does_not_load_os_backends():
    default_registry()
    assert "pyautogui" not in sys.modules
    assert "pywinauto" not in sys.modules
    assert "pyatspi" not in sys.modules
    assert "ApplicationServices" not in sys.modules
    assert "AppKit" not in sys.modules
    assert "PySide6" not in sys.modules


def test_slash_mouse_persists_and_stop(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setattr("hfagent.config.user_env_path", lambda: env_path)
    from hfagent.commands import HELP, dispatch_slash
    from hfagent.desktop import handle_command

    class UI(AgentUI):
        def __init__(self):
            self.status = []

        def on_status(self, text):
            self.status.append(text)

    ui = UI()
    agent = Agent(Config(), default_registry(), AgentUI())
    assert "/mouse" in HELP
    assert dispatch_slash(agent, ui, "/mouse") == "handled"
    assert "mouse off" in ui.status[-1]
    assert dispatch_slash(agent, ui, "/mouse on") == "handled"
    assert "mouse on" in ui.status[-1]
    assert "HFAGENT_MOUSE=1" in env_path.read_text(encoding="utf-8")
    assert dispatch_slash(agent, ui, "/mouse approval per-task") == "handled"
    assert get_session().approval_mode() == "per-task"
    assert "HFAGENT_MOUSE_APPROVAL=per-task" in env_path.read_text(encoding="utf-8")
    assert handle_command("stop") == "mouse emergency stop. No further actions until /mouse on."
    assert get_session().stopped
    assert dispatch_slash(agent, ui, "/mouse off") == "handled"
    assert ui.status[-1] == "mouse off. Nothing is watching the screen."
    assert handle_command("sideways") == (
        "usage: /mouse on|off | overlay on|off | approval per-action|per-task | log | stop"
    )


def test_config_report_mentions_mouse_and_vision(monkeypatch):
    monkeypatch.delenv("HFAGENT_MOUSE", raising=False)
    report = format_config_report(Config.load())
    assert "mouse=off" in report
    monkeypatch.setenv("HFAGENT_VISION_MODEL", "llama3.2-vision")
    monkeypatch.setenv("HFAGENT_VISION_API_KEY", "vision-secret-key")
    report = format_config_report(Config.load())
    assert "vision_model=llama3.2-vision" in report
    assert "vision-secret-key" not in report
    assert "visi…-key" in report or "visi…key" in report


def test_user_turn_starts_a_new_mouse_task(monkeypatch):
    calls = []
    monkeypatch.setattr("hfagent.desktop.begin_task", lambda: calls.append("turn"))
    agent = Agent(
        Config(api_key="fake", base_url="http://127.0.0.1:9", model="m"),
        default_registry(),
        AgentUI(),
    )
    monkeypatch.setattr(agent, "_step", lambda: {"role": "assistant", "content": "All set."})
    agent.run("hello")
    assert calls == ["turn"]


def test_prompt_tells_the_model_to_wait_for_mouse_on():
    assert "/mouse on" in SYSTEM_PROMPT
    assert "password" in SYSTEM_PROMPT
    assert "mouse_elements" in SYSTEM_PROMPT
    assert "open -a Safari" in SYSTEM_PROMPT
    assert "xdg-open" in SYSTEM_PROMPT
    assert 'start ""' in SYSTEM_PROMPT
    assert "They do nothing until" not in SYSTEM_PROMPT
    assert "ihmouse" in SYSTEM_PROMPT
    assert "qwen2.5vl" in vision_setup_message("gpt-oss:120b")


def test_system_prompt_reads_the_live_mouse_switch(monkeypatch):
    from hfagent.agent import system_prompt

    off = system_prompt("http://127.0.0.1:9/v1")
    assert "Mouse status:" in off
    assert "Mouse control is OFF" in off
    assert "Ask the user to run /mouse on" in off
    assert "open -a Safari https://mail.google.com" in off
    monkeypatch.setenv("HFAGENT_MOUSE", "1")
    on = system_prompt("http://127.0.0.1:9/v1")
    assert "Mouse control is ON" in on
    assert "call the mouse_* tools now" in on
    assert "Do not ask them to run /mouse on" in on
    get_session().trip("user /mouse stop")
    stopped = system_prompt("http://127.0.0.1:9/v1")
    assert "Mouse control is STOPPED" in stopped
    assert "Do not call mouse tools" in stopped


def test_mouse_toggle_updates_the_model_context(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setattr("hfagent.config.user_env_path", lambda: env_path)
    from hfagent.commands import dispatch_slash
    from hfagent.sessions import derive_title
    from hfagent.ui.tui import is_internal_nudge

    class UI(AgentUI):
        def on_status(self, text):
            return None

    agent = Agent(Config(), default_registry(), UI())
    assert "Mouse control is OFF" in agent.messages[0]["content"]
    before = len(agent.messages)

    assert dispatch_slash(agent, UI(), "/mouse on") == "handled"
    system = agent.messages[0]["content"]
    assert "Mouse control is ON" in system
    assert "call the mouse_* tools now" in system
    assert "Do not ask them to run /mouse on" in system
    note = agent.messages[-1]["content"]
    assert note.startswith("Mouse control is now ON")
    assert "call the tools on your next turn" in note
    assert "Mouse control is ON" in note
    assert is_internal_nudge(note)
    assert derive_title(agent.messages) == "untitled"
    assert len(agent.messages) == before + 1

    assert dispatch_slash(agent, UI(), "/mouse") == "handled"
    assert dispatch_slash(agent, UI(), "/mouse log") == "handled"
    assert dispatch_slash(agent, UI(), "/mouse approval per-task") == "handled"
    assert len(agent.messages) == before + 1

    assert dispatch_slash(agent, UI(), "/mouse overlay off") == "handled"
    assert "hiding the ihmouse overlay" in agent.messages[-1]["content"]
    assert "ihmouse overlay is off" in agent.messages[0]["content"]
    assert "Mouse control is ON" in agent.messages[0]["content"]

    assert dispatch_slash(agent, UI(), "/mouse overlay on") == "handled"
    assert agent.messages[-1]["content"].startswith("Mouse control is now showing")
    assert "ihmouse overlay is on" in agent.messages[0]["content"]

    assert dispatch_slash(agent, UI(), "/mouse stop") == "handled"
    assert agent.messages[-1]["content"].startswith("Mouse control is now stopped")
    assert "Mouse control is STOPPED" in agent.messages[0]["content"]
    assert is_internal_nudge(agent.messages[-1]["content"])

    assert dispatch_slash(agent, UI(), "/mouse off") == "handled"
    assert agent.messages[-1]["content"].startswith("Mouse control is now OFF")
    assert "Mouse control is OFF" in agent.messages[0]["content"]
    assert "Ask the user to run /mouse on" in agent.messages[0]["content"]
    assert derive_title(
        agent.messages + [{"role": "user", "content": "open Gmail in Safari"}]
    ) == "open Gmail in Safari"


def test_mouse_tool_descriptions_follow_the_switch(monkeypatch):
    click = default_registry().get("mouse_click")
    off = click.spec()["function"]["description"]
    assert off.startswith("Mouse control is OFF.")
    assert "Ask the user to run /mouse on" in off
    assert "Requires /mouse on" not in off
    assert "Does nothing" not in off
    assert "Click the focused app" in off

    _enable(monkeypatch)
    turned_on = click.spec()["function"]["description"]
    assert turned_on.startswith("Mouse control is ON.")
    assert "Call this tool when the user asked" in turned_on
    assert "Do not tell the user to run /mouse on" in turned_on

    get_session().trip("emergency stop")
    stopped = click.spec()["function"]["description"]
    assert stopped.startswith("Mouse control is STOPPED.")
    assert "Ask the user to run /mouse on" in stopped


def test_off_tool_tells_the_model_to_ask_for_mouse_on():
    pointer = Pointer()
    control, calls = _controller(pointer, lambda: _snapshot())
    result = control.perform("click", element=1)
    assert "Ask the user to run /mouse on" in result
    assert "mouse control is off" in result
    assert "then call this tool again" in result
    assert pointer.clicks == []
    assert calls["grab"] == 0


def test_new_session_sees_mouse_already_on(monkeypatch):
    monkeypatch.setenv("HFAGENT_MOUSE", "1")
    agent = Agent(Config(), default_registry(), AgentUI())
    assert "Mouse control is ON" in agent.messages[0]["content"]
    spec = agent.registry.get("mouse_type").spec()["function"]["description"]
    assert spec.startswith("Mouse control is ON.")
    assert "Do not tell the user to run /mouse on" in spec


def test_normalize_keys_maps_command_to_ctrl_off_macos():
    assert normalize_keys("command+c", "linux") == ["ctrl", "c"]
    assert normalize_keys("cmd+c", "darwin") == ["command", "c"]
    assert parse_hotkey("ctrl+alt+esc") == {"ctrl", "alt", "esc"}
    assert chord_down({"ctrl", "alt", "esc"}, {"ctrl", "alt", "esc", "shift"})
    assert not chord_down({"ctrl", "alt"}, {"ctrl"})
