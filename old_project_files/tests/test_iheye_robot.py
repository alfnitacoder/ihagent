"""Robot iheye skin: art split, pose, and a mock of the draw list.

AppKit is not imported. The overlay source is checked for the hooks the
helper uses on macOS.
"""

from pathlib import Path

import pytest

from hfagent.iheye.robot import (
    ASSETS,
    assets_ready,
    describe_frame,
    load_layout,
    normalize_skin,
    panel_size,
    point_in_frame,
    read_png,
    reset_layout_cache,
    resolve_skin,
    ring_cache_key,
    robot_pose,
    skin_from_env,
    split_robot_eye,
    write_png,
)
from hfagent.iheye.tracking import tick_interval


class MockRobotCanvas:
    """Records the draw list the overlay walks for one frame."""

    def __init__(self):
        self.calls = []

    def image(self, name, dx, dy, alpha):
        self.calls.append(("image", name, dx, dy, alpha))

    def clip(self, name):
        self.calls.append(("clip", name))

    def ring(self, rotation, cache, dashes):
        self.calls.append(("ring", rotation, cache, dashes))

    def core(self, alpha, scale):
        self.calls.append(("core", alpha, scale))

    def tint(self, rgb, alpha):
        self.calls.append(("tint", rgb, alpha))

    def lids(self, amount):
        self.calls.append(("lids", amount))


def _replay(canvas: MockRobotCanvas, pose) -> None:
    for op in describe_frame(pose):
        kind = op["op"]
        if kind == "image":
            canvas.image(op["name"], op.get("dx", 0.0), op.get("dy", 0.0), op.get("alpha", 1.0))
        elif kind == "clip":
            canvas.clip(op["name"])
        elif kind == "ring":
            canvas.ring(op["rotation"], op["cache"], op["dashes"])
        elif kind == "core":
            canvas.core(op["alpha"], op["scale"])
        elif kind == "tint":
            canvas.tint(op["rgb"], op["alpha"])
        elif kind == "lids":
            canvas.lids(op["amount"])


def _pixel(rgba: bytes, width: int, x: int, y: int) -> tuple[int, int, int, int]:
    i = (y * width + x) * 4
    return tuple(rgba[i : i + 4])  # type: ignore[return-value]


def _synthetic_rgb() -> tuple[bytes, int, int]:
    width, height = 90, 54
    buf = bytearray(width * height * 3)
    cx, cy = 45, 27
    for y in range(height):
        for x in range(width):
            dx, dy = x - cx, y - cy
            color = (0, 0, 0)
            if (dx / 40) ** 2 + (dy / 24) ** 2 <= 1:
                color = (150, 152, 156)
            if (dx / 28) ** 2 + (dy / 14) ** 2 <= 1:
                color = (8, 10, 16)
            if (dx / 16) ** 2 + (dy / 8) ** 2 <= 1:
                color = (40, 110, 210)
            if dx * dx + dy * dy <= 16:
                color = (250, 252, 255)
            i = (y * width + x) * 3
            buf[i : i + 3] = bytes(color)
    return bytes(buf), width, height


def test_skin_defaults_to_grok_and_classic_is_explicit():
    assert skin_from_env({}) == "grok"
    assert skin_from_env({"HFAGENT_IHEYE_SKIN": "  FACE "}) == "grok"
    assert skin_from_env({"HFAGENT_IHEYE_SKIN": "  CLASSIC "}) == "classic"
    assert skin_from_env({"HFAGENT_IHEYE_SKIN": "robot"}) == "robot"
    assert normalize_skin("nope") == "grok"
    assert normalize_skin("") == "grok"
    assert resolve_skin("classic", {"HFAGENT_IHEYE_SKIN": "robot"}) == "classic"
    assert resolve_skin("  ", {"HFAGENT_IHEYE_SKIN": "classic"}) == "classic"
    assert resolve_skin("face", {}) == "grok"
    assert panel_size("classic") == (88.0, 100.0)
    assert panel_size("grok")[0] == panel_size("grok")[1]
    width, height = panel_size("robot")
    assert 120 <= width <= 160
    assert height > 40


def test_robot_pose_spins_tints_and_blinks():
    idle = robot_pose("idle", 1.0)
    thinking = robot_pose("thinking", 1.0)
    assert thinking.spin_period < idle.spin_period
    assert abs(thinking.ring_radians) > abs(idle.ring_radians)
    assert thinking.glow > idle.glow
    assert thinking.tint_rgb[2] > thinking.tint_rgb[0]
    waiting = robot_pose("waiting", 0.4)
    assert waiting.tint_rgb[0] > waiting.tint_rgb[2]
    assert waiting.tint_alpha > 0.4
    assert waiting.blink == 0.0
    done = robot_pose("done", 0.4)
    assert done.tint_rgb[1] > done.tint_rgb[0]
    assert done.tint_alpha > 0.2
    assert robot_pose("done", 100).tint_alpha == 0.0
    assert 0.4 <= idle.breath <= 1.0
    blinked = robot_pose("idle", 0.1)
    assert blinked.blink > 0.5
    assert robot_pose("idle", 3.0).blink == 0.0
    assert ring_cache_key("thinking") == "thinking"
    assert ring_cache_key("nope") == "idle"


def test_gaze_moves_the_iris_inside_the_socket():
    layout = load_layout()
    quiet = robot_pose("idle", 0.2, pupil=(0.0, 0.0), layout=layout)
    looking = robot_pose("idle", 0.2, pupil=(6.0, 0.0), layout=layout)
    assert looking.iris_dx > quiet.iris_dx
    assert abs(looking.iris_dx) <= float(layout["travel"]["x"]) + 1e-6
    up = robot_pose("idle", 0.2, pupil=(0.0, 6.0), layout=layout)
    assert up.iris_dy > 0
    assert abs(up.iris_dy) <= float(layout["travel"]["y"]) + 1e-6
    canvas = MockRobotCanvas()
    _replay(canvas, looking)
    names = [call[0] for call in canvas.calls]
    assert names[0] == "image"
    assert "clip" in names
    assert names.count("ring") == 3
    assert "core" in names
    iris = next(call for call in canvas.calls if call[0] == "image" and call[1] == "iris")
    assert iris[2] == pytest.approx(looking.iris_dx)
    lids = robot_pose("idle", 0.1, layout=layout)
    canvas = MockRobotCanvas()
    _replay(canvas, lids)
    assert any(call[0] == "lids" and call[1] > 0.5 for call in canvas.calls)
    open_eye = robot_pose("waiting", 0.2, layout=layout)
    canvas = MockRobotCanvas()
    _replay(canvas, open_eye)
    assert all(call[0] != "lids" for call in canvas.calls)
    amber = next(call for call in canvas.calls if call[0] == "tint")
    assert amber[1][0] > 0.8


def test_split_keeps_a_dark_socket_and_a_movable_iris():
    rgb, width, height = _synthetic_rgb()
    frame, iris, measured = split_robot_eye(rgb, width, height)
    assert measured["radii"][0] > measured["radii"][1]
    corner = _pixel(frame, width, 0, 0)
    assert corner[3] == 0
    cx, cy = measured["core"]
    center = _pixel(frame, width, int(cx), int(cy))
    assert center[3] == 255
    assert (center[0] + center[1] + center[2]) / 3 < 40
    glow = _pixel(iris, width, int(cx), int(cy))
    assert glow[3] > 200
    assert glow[0] > 200 and glow[2] > 200
    assert _pixel(iris, width, 0, 0)[3] == 0
    metal = _pixel(frame, width, int(cx), max(0, int(cy - measured["radii"][1] - 6)))
    assert metal[3] == 255
    assert metal[0] > center[0] + 40


def test_png_roundtrip_and_packaged_assets(tmp_path):
    raw = bytes([10, 20, 30, 255, 0, 0, 0, 0, 1, 2, 3, 128, 9, 8, 7, 64])
    path = tmp_path / "swatch.png"
    write_png(path, 2, 2, raw)
    width, height, back = read_png(path)
    assert (width, height) == (2, 2)
    assert back == raw

    reset_layout_cache()
    assert assets_ready()
    layout = load_layout()
    logical_w, logical_h = layout["logical_size"]
    assert 120 <= logical_w <= 160
    pixel_w, pixel_h = layout["pixel_size"]
    assert pixel_w == pytest.approx(logical_w * 2, abs=1)
    assert pixel_h == pytest.approx(logical_h * 2, abs=1)
    frame_w, frame_h, frame = read_png(ASSETS / "frame.png")
    assert (frame_w, frame_h) == (pixel_w, pixel_h)
    socket = layout["socket"]
    sx = int(socket["cx"] * frame_w)
    sy = int(socket["cy"] * frame_h)
    center = _pixel(frame, frame_w, sx, sy)
    assert center[3] > 200
    assert (center[0] + center[1] + center[2]) / 3 < 45
    above = _pixel(
        frame,
        frame_w,
        sx,
        max(0, int((socket["cy"] - socket["ry"] - 0.06) * frame_h)),
    )
    assert above[3] > 200
    assert (above[0] + above[1] + above[2]) / 3 > (center[0] + center[1] + center[2]) / 3
    iris_w, iris_h, iris = read_png(ASSETS / "iris.png")
    glow = _pixel(iris, iris_w, iris_w // 2, iris_h // 2)
    assert glow[3] > 200
    assert (glow[0] + glow[1] + glow[2]) / 3 > 180
    assert _pixel(iris, iris_w, 0, 0)[3] < 20
    assert point_in_frame(logical_w / 2, logical_h / 2, logical_w, logical_h, layout["outline"])
    assert point_in_frame(1, 1, logical_w, logical_h, layout["outline"]) is False
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert "iheye/assets/frame.png" in text
    assert "iheye/assets/iris.png" in text


def test_robot_idle_ticks_faster_than_classic_but_stays_cheap():
    common = dict(
        visible=True,
        state="idle",
        phase=1.0,
        tracking=True,
        pointer_near_eye=False,
        mouse_moved=False,
        typing=False,
    )
    assert tick_interval(**common) == pytest.approx(1 / 8)
    assert tick_interval(**common, skin="grok") == pytest.approx(1 / 24)
    assert tick_interval(**common, skin="robot") == pytest.approx(1 / 15)
    assert tick_interval(**{**common, "state": "thinking"}, skin="grok") == pytest.approx(1 / 30)


def test_helper_command_passes_skin_and_overlay_keeps_python_methods():
    from hfagent.iheye.__main__ import build_parser
    from hfagent.iheye.supervisor import helper_command
    from hfagent.iheye.host import HostInfo

    host = HostInfo(
        kind="terminal",
        app_name="Terminal",
        bundle_id="com.apple.Terminal",
        pid=1,
        ancestor_pids=(),
        tty="",
        source="args",
    )
    cmd = helper_command(
        parent_pid=1,
        socket_path="/tmp/eye.sock",
        position_path="/tmp/pos.json",
        host=host,
        skin="classic",
    )
    assert cmd[cmd.index("--skin") + 1] == "classic"
    args = build_parser().parse_args(cmd[3:])
    assert args.skin == "classic"
    default = helper_command(
        parent_pid=1,
        socket_path="/tmp/eye.sock",
        position_path="/tmp/pos.json",
        host=host,
    )
    assert default[default.index("--skin") + 1] == "grok"

    overlay = Path(__file__).resolve().parents[1] / "src" / "hfagent" / "iheye" / "overlay_app.py"
    source = overlay.read_text(encoding="utf-8")
    assert "def set_motion" in source
    assert "def setEyeState_" in source
    assert "def set_speaking" in source
    assert "@objc.python_method" in source
    assert "describe_frame" in source
    assert "describe_face" in source
    assert "def _paint_face" in source
    assert "use_skin" in source
    assert "setWantsLayer_" not in source
    assert "setLayerContentsRedrawPolicy_" not in source
    assert "CALayer" not in source
    assert "charactersIgnoringModifiers" not in source


def test_config_report_names_the_skin(monkeypatch):
    from hfagent.config import format_config_report, Config

    monkeypatch.delenv("HFAGENT_IHEYE_SKIN", raising=False)
    report = format_config_report(Config.load())
    assert "iheye_skin=grok" in report
    monkeypatch.setenv("HFAGENT_IHEYE_SKIN", "classic")
    report = format_config_report(Config.load())
    assert "iheye_skin=classic" in report
