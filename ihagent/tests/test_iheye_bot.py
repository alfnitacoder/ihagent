"""Bot iheye skin: moods, poses, and the drawRect_ painter."""

from pathlib import Path
from types import SimpleNamespace

from hfagent.agent import Agent, AgentUI
from hfagent.config import Config
from hfagent.iheye.appearance import STATES
from hfagent.iheye.bot import (
    BOT_H,
    BOT_W,
    IDLE_ANIMATIONS,
    IDLE_GAP,
    IDLE_NAMES,
    IDLE_PLAY,
    IDLE_TIMEOUT,
    SUCCESS_PHASE,
    bot_mood,
    bot_pose,
    describe_bot,
    point_in_bot,
    reduce_motion_from_env,
)
from hfagent.iheye.tracking import PHASE_PER_SEC
from hfagent.iheye.overlay_app import overlay_reduce_motion
from hfagent.iheye.supervisor import EyeSupervisor
from hfagent.tools import default_registry


def _ops(pose):
    return describe_bot(pose)


def test_listening_is_never_selected_by_agent_hooks():
    assert "listening" not in STATES
    for state in STATES:
        for speaking in (False, True):
            for working in (False, True):
                for failed in (False, True):
                    assert (
                        bot_mood(state, speaking=speaking, working=working, failed=failed)
                        != "listening"
                    )


def test_mood_maps_existing_hooks():
    assert bot_mood("idle") == "idle"
    assert bot_mood("thinking") == "thinking"
    assert bot_mood("thinking", speaking=True) == "speaking"
    assert bot_mood("thinking", speaking=True, working=True) == "working"
    assert bot_mood("thinking", working=True) == "working"
    assert bot_mood("waiting") == "error"
    assert bot_mood("waiting", working=True) == "error"
    assert bot_mood("done") == "success"
    assert bot_mood("done", failed=True) == "error"
    assert bot_mood("thinking", failed=True) == "error"
    assert bot_mood("nope") == "idle"


def test_seven_visual_poses_differ():
    frames = {
        "idle": bot_pose("idle", 2.2),
        "thinking": bot_pose("thinking", 1.4),
        "listening": bot_pose("idle", 1.0, mood="listening"),
        "speaking": bot_pose("thinking", 1.05, speaking=True),
        "working": bot_pose("thinking", 1.3, working=True),
        "success": bot_pose("done", 1.15),
        "error": bot_pose("waiting", 0.9),
    }
    assert frames["listening"].mood == "listening"
    signatures = []
    for name, pose in frames.items():
        ops = _ops(pose)
        assert ops, name
        assert any(op["op"] == "roundrect" for op in ops)
        assert any(op["op"] == "poly" for op in ops)
        signatures.append(
            (
                pose.mood,
                round(pose.tilt, 1),
                round(pose.smile, 2),
                round(pose.mouth_open, 2),
                round(pose.chest, 2),
                round(pose.ring_speed, 2),
                tuple(round(c, 2) for c in pose.led_rgb),
            )
        )
    assert len(set(signatures)) == 7
    assert frames["thinking"].tilt < -8
    assert frames["working"].ring_speed > frames["idle"].ring_speed
    assert frames["working"].chest > 0.8
    assert frames["speaking"].mouth_open > 0.3
    assert frames["success"].smile > 0.8
    assert frames["success"].wave > 0.5
    assert frames["error"].led_rgb[0] > frames["error"].led_rgb[2]
    assert frames["idle"].led_rgb[2] > frames["idle"].led_rgb[0]
    assert frames["error"].smile < 0


def test_success_settles_back_to_idle():
    celebrating = bot_pose("done", 1.2)
    settled = bot_pose("done", SUCCESS_PHASE + 1.0)
    assert celebrating.mood == "success"
    assert celebrating.state == "done"
    assert settled.mood == "idle"
    assert settled.state == "done"
    assert settled.wave < celebrating.wave
    failed = bot_pose("done", 1.2, failed=True)
    assert failed.mood == "error"


def test_reduced_motion_holds_the_pose():
    assert reduce_motion_from_env({}) is False
    assert reduce_motion_from_env({"HFAGENT_IHEYE_REDUCE_MOTION": "1"}) is True
    assert reduce_motion_from_env({"HFAGENT_IHEYE_REDUCE_MOTION": "off"}) is False
    assert overlay_reduce_motion({}) is False
    assert overlay_reduce_motion({"HFAGENT_IHEYE_REDUCE_MOTION": "yes"}) is True
    blinking = bot_pose("idle", 0.3, blink_extra=0.8)
    still_a = bot_pose("idle", 0.3, blink_extra=0.8, reduce_motion=True)
    still_b = bot_pose("idle", 4.5, reduce_motion=True)
    assert blinking.blink > 0.5
    assert still_a.blink == 0.0
    assert still_a.bob == still_b.bob == 0.0
    assert still_a.wave == still_b.wave == 0.0
    assert still_a.ring == still_b.ring
    assert still_a.scan == 0.0
    # The pointer still moves the eyes. That is tracking, not a looping animation.
    looked = bot_pose("idle", 1.0, pupil=(6.0, 0.0), reduce_motion=True)
    assert looked.gaze_x > 2.0
    success = bot_pose("done", 1.2, reduce_motion=True)
    assert success.mood == "success"
    assert success.wave == 0.0
    assert success.bob == 0.0
    assert success.smile > 0.8


def _idle_at(index: int, frac: float):
    elapsed = IDLE_TIMEOUT + index * (IDLE_PLAY + IDLE_GAP) + IDLE_PLAY * frac
    return bot_pose("idle", 0.0, reduce_motion=False, idle_for=elapsed)


def test_idle_flourishes_rotate_and_stop_when_activity_resumes():
    assert IDLE_NAMES == ("sway", "hop", "wave", "spin", "breathe")
    assert len({name for name, _fn in IDLE_ANIMATIONS}) == 5
    for _name, fn in IDLE_ANIMATIONS:
        start = fn(0.0, IDLE_PLAY)
        end = fn(IDLE_PLAY, IDLE_PLAY)
        for delta in (start, end):
            assert abs(delta.bob) < 0.05
            assert abs(delta.tilt) < 0.05
            assert abs(delta.wave) < 0.05
            assert abs(delta.chest) < 0.05
            assert abs(delta.ring) < 0.05
        energy = 0.0
        for frac in (0.12, 0.25, 0.37, 0.62, 0.75):
            delta = fn(IDLE_PLAY * frac, IDLE_PLAY)
            energy = max(
                energy,
                abs(delta.bob) + abs(delta.tilt) + abs(delta.wave) + abs(delta.chest) + abs(delta.ring),
            )
        assert energy > 0.5

    resting = bot_pose("idle", 0.0, idle_for=0.0)
    assert resting.idle_anim == ""
    assert bot_pose("idle", 1.0).idle_anim == ""
    assert bot_pose("idle", (IDLE_TIMEOUT - 0.2) * PHASE_PER_SEC).idle_anim == ""

    sway_a = _idle_at(0, 0.125)
    sway_b = _idle_at(0, 0.375)
    assert sway_a.idle_anim == sway_b.idle_anim == "sway"
    assert sway_a.tilt * sway_b.tilt < 0
    assert abs(sway_a.tilt) > 3 and abs(sway_b.tilt) > 3

    hop = _idle_at(1, 0.5)
    assert hop.idle_anim == "hop"
    assert hop.bob > resting.bob + 6

    wave = _idle_at(2, 0.5)
    assert wave.idle_anim == "wave"
    assert wave.wave > 0.6

    spin = _idle_at(3, 0.5)
    assert spin.idle_anim == "spin"
    assert spin.tilt > 10
    assert spin.ring > resting.ring + 0.8

    breathe = _idle_at(4, 0.25)
    assert breathe.idle_anim == "breathe"
    assert breathe.chest > resting.chest + 0.25
    assert breathe.bob < hop.bob - 4

    gap = bot_pose("idle", 0.0, idle_for=IDLE_TIMEOUT + IDLE_PLAY + 0.15)
    assert gap.idle_anim == ""
    again = bot_pose(
        "idle",
        0.0,
        idle_for=IDLE_TIMEOUT + 5 * (IDLE_PLAY + IDLE_GAP) + 0.2,
    )
    assert again.idle_anim == "sway"

    # The overlay only passes phase. A long idle phase still picks a flourish.
    from_phase = bot_pose("idle", (IDLE_TIMEOUT + 0.3) * PHASE_PER_SEC)
    assert from_phase.idle_anim == "sway"
    settled_done = bot_pose("done", SUCCESS_PHASE + (IDLE_TIMEOUT + 0.3) * PHASE_PER_SEC)
    assert settled_done.mood == "idle"
    assert settled_done.idle_anim == "sway"
    assert bot_pose("done", SUCCESS_PHASE + 1.0).idle_anim == ""

    for pose in (
        bot_pose("thinking", 0.0, idle_for=99),
        bot_pose("thinking", 0.0, speaking=True, idle_for=99),
        bot_pose("thinking", 0.0, working=True, idle_for=99),
        bot_pose("waiting", 0.0, idle_for=99),
        bot_pose("done", 1.0, idle_for=99),
        bot_pose("idle", 0.0, reduce_motion=True, idle_for=99),
    ):
        assert pose.idle_anim == ""

    rest_ops = describe_bot(resting)
    hop_ops = describe_bot(hop)
    rest_top = max(op["cy"] for op in rest_ops if "cy" in op)
    hop_top = max(op["cy"] for op in hop_ops if "cy" in op)
    assert hop_top > rest_top + 4


def test_clicks_outside_the_robot_fall_through():
    assert point_in_bot(BOT_W / 2, 146, BOT_W, BOT_H)
    assert point_in_bot(1, 1, BOT_W, BOT_H) is False
    assert point_in_bot(BOT_W - 1, BOT_H - 1, BOT_W, BOT_H) is False
    wide = describe_bot(bot_pose("idle", 1.0), BOT_W * 2, BOT_H)
    assert any(op["cx"] > BOT_W for op in wide if "cx" in op)


def test_overlay_paints_the_bot_without_layers():
    overlay = Path(__file__).resolve().parents[1] / "src" / "hfagent" / "iheye" / "overlay_app.py"
    source = overlay.read_text(encoding="utf-8")
    paint = source.split("def _paint_bot", 1)[1].split("\n        def ", 1)[0]
    assert "describe_bot" in paint
    assert "bot_pose" in paint
    draw = source.split("def drawRect_", 1)[1].split("\n        def ", 1)[0]
    assert '_skin == "bot"' in draw
    assert "_paint_bot" in draw
    assert '_skin == "grok"' in draw
    assert "CALayer" not in source
    assert "setWantsLayer_" not in source
    assert "setLayerContentsRedrawPolicy_" not in source
    handle = source.split("def _handle", 1)[1].split("\n        def ", 1)[0]
    assert "set_speaking" in handle
    assert '"working"' in handle
    assert '"failed"' in handle
    assert "set_activity" in handle
    assert "accessibilityDisplayShouldReduceMotion" in source


def test_activity_flags_queue_without_new_states():
    control = EyeSupervisor(platform_name="linux")
    control._proc = SimpleNamespace(poll=lambda: None)
    control.note_activity(working=True)
    assert control._pending_msgs["activity"]["working"] is True
    control.set_state("thinking")
    assert control._working is False
    assert control._failed is False
    control.note_activity(failed=True)
    control.set_state("done")
    assert control.state == "done"
    assert control._failed is True


def test_tool_run_and_errors_mark_the_bot(monkeypatch):
    flags = []
    monkeypatch.setattr("hfagent.iheye.publish_activity", lambda **kw: flags.append(kw))

    class UI(AgentUI):
        def __init__(self):
            self.errors = []
            self.actions = []

        def on_action(self, name, args):
            self.actions.append(name)

        def on_observation(self, name, result):
            return None

        def on_error(self, message):
            self.errors.append(message)

    ui = UI()
    agent = Agent(Config(approval="auto"), default_registry(), ui)
    tool = SimpleNamespace(needs_approval=False, run=lambda **_kwargs: "found")
    agent.registry.get = lambda name: tool if name == "grep" else None
    assert agent._execute({"function": {"name": "grep", "arguments": "{\"pattern\": \"a\"}"}}) == "found"
    assert flags == [{"working": True}]
    agent._note_eye_working(False)
    agent._note_eye_error("stopped")
    assert ui.errors == ["stopped"]
    assert {"working": False} in flags
    assert {"failed": True} in flags

    monkeypatch.setattr("hfagent.iheye.publish", lambda _state: None)
    monkeypatch.setattr("hfagent.iheye.publish_summary", lambda _text: None)

    def boom(_text):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent, "_run", boom)
    flags.clear()
    try:
        agent.run("hi")
    except RuntimeError:
        pass
    else:
        raise AssertionError("run should re-raise")
    assert {"failed": True} in flags
