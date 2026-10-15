"""Grok-style iheye face: pose, draw list, and the safe paint path.

AppKit is not imported. The overlay source is checked so the face stays on
the drawRect path the classic bubble already uses.
"""

from pathlib import Path

from hfagent.iheye.face import (
    FACE_H,
    FACE_W,
    describe_face,
    face_mood,
    face_pose,
    point_in_face,
)


def _ops(pose, width=FACE_W, height=FACE_H):
    return describe_face(pose, width, height)


def _kind(ops, name):
    return [op for op in ops if op["op"] == name]


def test_mood_uses_a_streaming_reply_as_speech():
    assert face_mood("thinking", speaking=False) == "thinking"
    assert face_mood("thinking", speaking=True) == "speaking"
    assert face_mood("idle", speaking=True) == "idle"
    assert face_mood("nope") == "idle"


def test_idle_blinks_and_breathes():
    closed = face_pose("idle", 0.42)
    opened = face_pose("idle", 3.0)
    assert closed.blink > 0.15
    assert opened.blink == 0.0
    assert opened.mood == "idle"
    shut = _kind(_ops(closed), "eye")
    wide = _kind(_ops(opened), "eye")
    assert len(shut) == 2 and len(wide) == 2
    assert shut[0]["ry"] < wide[0]["ry"] * 0.55
    assert _kind(_ops(closed), "pupil") == []
    assert len(_kind(_ops(opened), "pupil")) == 2
    assert face_pose("idle", 0.0).scale != face_pose("idle", 2.4).scale
    assert 0.97 <= opened.scale <= 1.03


def test_gaze_moves_the_eyes_and_stays_inside_the_disc():
    still = face_pose("idle", 2.0, pupil=(0.0, 0.0))
    looking = face_pose("idle", 2.0, pupil=(6.0, 0.0))
    up = face_pose("idle", 2.0, pupil=(0.0, 6.0))
    assert looking.gaze_x > still.gaze_x + 3.0
    assert up.gaze_y > still.gaze_y + 2.0
    assert abs(looking.gaze_x) <= 7.0 + 1e-6
    assert abs(up.gaze_y) <= 4.6 + 1e-6
    still_pupils = _kind(_ops(still), "pupil")
    look_pupils = _kind(_ops(looking), "pupil")
    assert look_pupils[0]["cx"] > still_pupils[0]["cx"]
    assert look_pupils[1]["cx"] > still_pupils[1]["cx"]
    for op in _ops(looking):
        if "cx" in op:
            assert -2.0 <= op["cx"] <= FACE_W + 2.0
            assert -2.0 <= op["cy"] <= FACE_H + 2.0


def test_states_read_as_different_faces():
    idle = face_pose("idle", 2.0, pupil=(0.0, 0.0))
    thinking = face_pose("thinking", 2.0, pupil=(0.0, 0.0))
    speaking = face_pose("thinking", 1.2, pupil=(0.0, 0.0), speaking=True)
    waiting = face_pose("waiting", 0.4)
    done = face_pose("done", 0.4)
    assert thinking.eye_open < idle.eye_open
    assert thinking.gaze_y > idle.gaze_y + 1.0
    assert thinking.mouth_open < 0.25
    opens = [
        face_pose("thinking", i * 0.15, speaking=True).mouth_open for i in range(24)
    ]
    assert max(opens) > 0.7
    assert speaking.mood == "speaking"
    assert max(opens) > thinking.mouth_open + 0.4
    talk = _ops(speaking)
    assert _kind(talk, "mouth_blob")
    assert waiting.blink == 0.0
    assert waiting.rim_rgb[0] > waiting.rim_rgb[2]
    assert waiting.rim_alpha > 0.4
    assert waiting.eye_open > idle.eye_open
    assert done.smile > 0.7
    assert done.eye_open < idle.eye_open
    assert done.rim_rgb[1] > done.rim_rgb[0]
    assert done.rim_alpha > 0.2
    assert face_pose("done", 100).rim_alpha == 0.0
    assert _kind(_ops(done), "mouth_arc")
    assert _kind(_ops(idle), "mouth_arc")
    assert _kind(_ops(idle), "disc")


def test_keyboard_blink_adds_to_the_idle_one():
    calm = face_pose("idle", 3.0, blink_extra=0.0)
    glanced = face_pose("idle", 3.0, blink_extra=0.8)
    assert calm.blink == 0.0
    assert glanced.blink > 0.7


def test_clicks_outside_the_circle_fall_through():
    assert point_in_face(FACE_W / 2, FACE_H / 2, FACE_W, FACE_H)
    assert point_in_face(1, 1, FACE_W, FACE_H) is False
    assert point_in_face(FACE_W - 1, FACE_H - 1, FACE_W, FACE_H) is False


def test_overlay_paints_the_face_without_layers():
    overlay = Path(__file__).resolve().parents[1] / "src" / "hfagent" / "iheye" / "overlay_app.py"
    source = overlay.read_text(encoding="utf-8")
    paint = source.split("def _paint_face", 1)[1].split("\n        def ", 1)[0]
    assert "describe_face" in paint
    assert "face_pose" in paint
    draw = source.split("def drawRect_", 1)[1].split("\n        def ", 1)[0]
    assert '_skin == "grok"' in draw
    assert "_paint_face" in draw
    assert "setNeedsDisplay_" in source
    handle = source.split("def _handle", 1)[1].split("\n        def ", 1)[0]
    assert "set_speaking" in handle
