"""The web robot bridge follows real agent phases and does not invent audio."""

import json
import threading
import urllib.request

from hfagent.iheye import publish
from hfagent.robot_bridge import Bridge, attach_ui, bridge_enabled


class _UI:
    def on_error(self, message):
        pass

    def on_action(self, tool_name, arguments):
        pass


def test_bridge_off_unless_enabled(monkeypatch):
    monkeypatch.delenv("HFAGENT_ROBOT_BRIDGE", raising=False)
    assert bridge_enabled() is False
    for value in ("1", "true", "yes", "on"):
        assert bridge_enabled({"HFAGENT_ROBOT_BRIDGE": value}) is True
    assert bridge_enabled({"HFAGENT_ROBOT_BRIDGE": "0"}) is False


def test_iheye_phases_map_without_audio():
    bridge = Bridge()
    bridge.note_iheye("thinking")
    assert bridge.state == "thinking"
    assert bridge.message == ""
    bridge.note_iheye("waiting")
    assert bridge.state == "idle"
    bridge.note_iheye("not-a-state")
    assert bridge.state == "idle"
    bridge.note_iheye("done")
    assert bridge.state == "success"
    assert bridge.mic is False
    assert bridge.audio is False


def test_failure_stays_error_when_the_turn_ends():
    bridge = Bridge()
    bridge.note_iheye("thinking")
    bridge.note_error("Cannot reach host")
    bridge.note_iheye("done")
    assert bridge.state == "error"
    assert "Cannot reach host" in bridge.message
    assert bridge.audio is False


def test_tool_start_is_working_and_text_is_not_speech():
    bridge = Bridge()
    bridge.note_iheye("thinking")
    bridge.note_working("read_file")
    assert bridge.state == "working"
    assert bridge.message == "Running read_file"
    bridge.note_assistant("Hello", final=False)
    bridge.note_assistant("Hello there", final=True)
    assert bridge.state == "working"
    assert bridge.state != "speaking"
    assert bridge.message == "Hello there"
    assert bridge.mic is False
    assert bridge.audio is False


def test_publish_reaches_the_bridge(monkeypatch):
    fresh = Bridge()
    monkeypatch.setattr("hfagent.robot_bridge.bridge", fresh)
    publish("thinking")
    assert fresh.state == "thinking"
    publish("waiting")
    assert fresh.state == "idle"
    publish("done")
    assert fresh.state == "success"
    publish("not-a-state")
    assert fresh.state == "success"


def test_attach_ui_reports_errors_and_tools():
    fresh = Bridge()
    ui = _UI()
    seen = []

    def on_error(message):
        seen.append(message)

    ui.on_error = on_error
    import hfagent.robot_bridge as rb

    previous = rb.bridge
    rb.bridge = fresh
    try:
        attach_ui(ui)
        ui.on_error("boom")
        ui.on_action("grep", {"pattern": "x"})
    finally:
        rb.bridge = previous
    assert seen == ["boom"]
    assert fresh.state == "working"
    assert fresh.message == "Running grep"


def test_submit_chat_needs_a_live_handler():
    bridge = Bridge()
    ok, err = bridge.submit_chat("hello", handler=None)
    assert ok is False
    assert err
    seen = []
    ok, err = bridge.submit_chat("hello", handler=lambda text: seen.append(text))
    assert ok is True
    assert seen == ["hello"]
    ok, err = bridge.submit_chat("  ", handler=lambda text: None)
    assert ok is False


def test_http_health_and_message():
    bridge = Bridge()
    assert bridge.start(port=0)
    port = bridge.port
    seen = []
    bridge.chat_handler = lambda text: seen.append(text)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as res:
            body = json.loads(res.read().decode("utf-8"))
        assert body["ok"] is True
        assert body["mic"] is False
        assert body["audio"] is False

        data = json.dumps({"text": "ping"}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/message",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=2) as res:
            posted = json.loads(res.read().decode("utf-8"))
        assert posted["ok"] is True
        assert seen == ["ping"]

        frames = []

        def read_events():
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/events", timeout=2) as res:
                frames.append(res.readline())
                frames.append(res.readline())

        thread = threading.Thread(target=read_events, daemon=True)
        thread.start()
        thread.join(2)
        assert frames and frames[0].startswith(b"data: ")
        payload = json.loads(frames[0][len(b"data: "):].decode("utf-8"))
        assert payload["type"] == "state"
        assert payload["mic"] is False
        assert payload["audio"] is False
    finally:
        bridge.stop()
