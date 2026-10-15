"""Robot iheye skin: art split, pose, and draw description.

The overlay paints this. Nothing here imports AppKit. ``split_robot_eye``
turns the source painting into a metal frame (socket filled dark, outside
the frame transparent) and a glow sprite the iris can move. Dashed rings,
the core flare, and the state tint are drawn on top so they can spin and
breathe without redrawing the metal.
"""

from __future__ import annotations

import json
import math
import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from .appearance import blink_amount, normalize_state, pulse_alpha, pupil_offset
from .tracking import PHASE_PER_SEC

ASSETS = Path(__file__).resolve().parent / "assets"
SKINS = ("bot", "grok", "classic", "robot")
DEFAULT_SKIN = "bot"

# Classic bubble, in points. The robot window size comes from layout.json.
CLASSIC_W = 88.0
CLASSIC_H = 100.0

# Retina assets are drawn at this scale. Logical width stays inside 120–160.
TARGET_WIDTH = 300
PIXEL_SCALE = 2
# Classic pupil offsets are a few points. Map that reach onto the socket.
CLASSIC_REACH = 6.0

_TINT = {
    "idle": (0.28, 0.66, 1.00),
    "thinking": (0.45, 0.84, 1.00),
    "waiting": (1.00, 0.62, 0.14),
    "done": (0.28, 0.95, 0.48),
}
# Radians of ring travel per phase unit. Thinking is several times idle.
_SPIN = {
    "idle": 0.16,
    "thinking": 0.78,
    "waiting": 0.30,
    "done": 0.42,
}
METAL_LID = (0.62, 0.64, 0.68)

_LAYOUT_CACHE: dict | None = None


@dataclass(frozen=True)
class RingSpec:
    """One dashed ring. ``radius`` is a fraction of the iris sprite."""

    radius: float
    width: float
    rgba: tuple[float, float, float, float]
    dashes: int
    duty: float
    spin: float


@dataclass(frozen=True)
class RobotPose:
    """One frame of the robot eye. Distances are overlay points."""

    state: str
    iris_dx: float
    iris_dy: float
    ring_radians: float
    spin_period: float
    breath: float
    core_scale: float
    blink: float
    tint_rgb: tuple[float, float, float]
    tint_alpha: float
    glow: float


def normalize_skin(value: object) -> str:
    """``bot`` is the default. ``grok`` (alias ``face``), ``classic``, and ``robot`` stay selectable."""
    text = str(value or "").strip().lower()
    if text == "bot":
        return "bot"
    if text in {"grok", "face"}:
        return "grok"
    if text == "classic":
        return "classic"
    if text == "robot":
        return "robot"
    return DEFAULT_SKIN


def skin_from_env(environ: dict[str, str] | None = None) -> str:
    """``HFAGENT_IHEYE_SKIN`` — ``bot`` (default), ``grok``, ``face``, ``classic``, or ``robot``."""
    env = os.environ if environ is None else environ
    return normalize_skin(env.get("HFAGENT_IHEYE_SKIN"))


def resolve_skin(explicit: object = None, environ: dict[str, str] | None = None) -> str:
    """A non-empty ``--skin`` wins. Otherwise the env var, otherwise bot."""
    if explicit is not None and str(explicit).strip():
        return normalize_skin(explicit)
    return skin_from_env(environ)


def assets_ready() -> bool:
    return (
        (ASSETS / "frame.png").is_file()
        and (ASSETS / "iris.png").is_file()
        and (ASSETS / "layout.json").is_file()
    )


def load_layout() -> dict:
    """Socket, iris home, and hit outline. Cached after the first read."""
    global _LAYOUT_CACHE
    if _LAYOUT_CACHE is not None:
        return _LAYOUT_CACHE
    path = ASSETS / "layout.json"
    if not path.is_file():
        _LAYOUT_CACHE = _default_layout()
        return _LAYOUT_CACHE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = _default_layout()
    if not isinstance(data, dict):
        data = _default_layout()
    _LAYOUT_CACHE = data
    return data


def reset_layout_cache() -> None:
    global _LAYOUT_CACHE
    _LAYOUT_CACHE = None


def _default_layout() -> dict:
    return {
        "logical_size": [150.0, 90.0],
        "pixel_size": [300, 180],
        "scale": PIXEL_SCALE,
        "socket": {"cx": 0.50, "cy": 0.50, "rx": 0.24, "ry": 0.16},
        "iris": {"x": 0.32, "y": 0.36, "w": 0.36, "h": 0.28},
        "travel": {"x": 8.0, "y": 5.0},
        "outline": [[0.5, 0.02], [0.96, 0.28], [0.96, 0.72], [0.5, 0.98], [0.04, 0.72], [0.04, 0.28]],
    }


def panel_size(skin: str) -> tuple[float, float]:
    name = normalize_skin(skin)
    if name == "robot":
        layout = load_layout()
        size = layout.get("logical_size") or [150.0, 90.0]
        return float(size[0]), float(size[1])
    if name == "classic":
        return CLASSIC_W, CLASSIC_H
    if name == "grok":
        from .face import FACE_H, FACE_W

        return FACE_W, FACE_H
    from .bot import BOT_H, BOT_W

    return BOT_W, BOT_H


def _mix(a: tuple[float, ...], b: tuple[float, ...], t: float) -> tuple[float, float, float]:
    return tuple(a[i] * (1.0 - t) + b[i] * t for i in range(3))  # type: ignore[return-value]


def ring_specs(state: str) -> tuple[RingSpec, ...]:
    """Crisp dashed rings. Colors lean toward the state tint."""
    state = normalize_state(state)
    tint = _TINT[state]
    mix = {"idle": 0.18, "thinking": 0.62, "waiting": 0.78, "done": 0.72}[state]
    alpha = {"idle": 0.78, "thinking": 0.95, "waiting": 0.88, "done": 0.9}[state]
    outer = _mix((0.30, 0.72, 1.00), tint, mix)
    violet = _mix((0.55, 0.42, 1.00), tint, mix)
    inner = _mix((0.82, 0.94, 1.00), tint, mix * 0.45)
    return (
        RingSpec(0.90, 1.5, (*outer, alpha), 26, 0.42, 1.0),
        RingSpec(0.70, 1.15, (*violet, alpha * 0.9), 18, 0.36, -0.62),
        RingSpec(0.48, 0.9, (*inner, min(1.0, alpha + 0.05)), 12, 0.30, 1.35),
    )


def ring_cache_key(state: str) -> str:
    """Ring sprites are rebuilt only when the tint changes."""
    return normalize_state(state)


def _spin_period(state: str) -> float:
    rad_per_sec = PHASE_PER_SEC * _SPIN[normalize_state(state)]
    return (2.0 * math.pi) / rad_per_sec


def _clamp_travel(dx: float, dy: float, travel_x: float, travel_y: float) -> tuple[float, float]:
    travel_x = max(0.0, travel_x)
    travel_y = max(0.0, travel_y)
    if travel_x == 0.0 and travel_y == 0.0:
        return 0.0, 0.0
    dist = math.hypot(dx / max(travel_x, 1e-6), dy / max(travel_y, 1e-6))
    if dist <= 1.0 or dist == 0.0:
        return max(-travel_x, min(travel_x, dx)), max(-travel_y, min(travel_y, dy))
    return dx / dist, dy / dist


def robot_pose(
    state: str,
    phase: float,
    *,
    pupil: tuple[float, float] | None = None,
    blink_extra: float = 0.0,
    layout: dict | None = None,
) -> RobotPose:
    """Pose for one overlay tick.

    ``pupil`` is the classic-scale offset from tracking (or None, in which
    case the state's own glance is used). It is scaled into the socket.
    """
    state = normalize_state(state)
    layout = layout if layout is not None else load_layout()
    travel = layout.get("travel") or {}
    travel_x = float(travel.get("x", 8.0))
    travel_y = float(travel.get("y", 5.0))
    if pupil is None:
        base = pupil_offset(state, phase)
    else:
        base = (float(pupil[0]), float(pupil[1]))
    dx, dy = _clamp_travel(
        base[0] / CLASSIC_REACH * travel_x,
        base[1] / CLASSIC_REACH * travel_y,
        travel_x,
        travel_y,
    )
    rate = 1.7 if state == "thinking" else 0.75
    wave = 0.5 + 0.5 * math.sin(phase * rate)
    breath = 0.55 + 0.45 * wave
    glow = 1.0
    if state == "thinking":
        breath = min(1.0, breath * 1.12)
        glow = 1.28
    pulse = pulse_alpha(state, phase)
    tint = _TINT[state]
    if state == "waiting":
        tint_alpha = 0.25 + 0.7 * pulse
    elif state == "done":
        tint_alpha = pulse
    elif state == "thinking":
        tint_alpha = 0.48
    else:
        tint_alpha = 0.14
    blink = min(1.0, blink_amount(state, phase) + max(0.0, float(blink_extra or 0.0)))
    return RobotPose(
        state=state,
        iris_dx=dx,
        iris_dy=dy,
        ring_radians=phase * _SPIN[state],
        spin_period=_spin_period(state),
        breath=breath,
        core_scale=0.88 + 0.20 * breath,
        blink=blink,
        tint_rgb=tint,
        tint_alpha=tint_alpha,
        glow=glow,
    )


def describe_frame(pose: RobotPose) -> tuple[dict, ...]:
    """Draw ops shared by the tests and the AppKit fallback painter.

    The layer renderer caches the frame, the iris sprite, and the ring
    images, then applies the translation, rotation, opacity, and lid values.
    """
    ops: list[dict] = [
        {"op": "image", "name": "frame", "alpha": 1.0},
        {"op": "clip", "name": "socket"},
        {
            "op": "image",
            "name": "iris",
            "dx": pose.iris_dx,
            "dy": pose.iris_dy,
            "alpha": pose.glow,
        },
    ]
    for spec in ring_specs(pose.state):
        ops.append(
            {
                "op": "ring",
                "radius": spec.radius,
                "width": spec.width,
                "rgba": spec.rgba,
                "dashes": spec.dashes,
                "duty": spec.duty,
                "rotation": pose.ring_radians * spec.spin,
                "cache": ring_cache_key(pose.state),
            }
        )
    ops.append(
        {
            "op": "core",
            "alpha": pose.breath,
            "scale": pose.core_scale,
            "rgb": (1.0, 1.0, 1.0),
        }
    )
    if pose.tint_alpha > 0.02:
        ops.append({"op": "tint", "rgb": pose.tint_rgb, "alpha": pose.tint_alpha})
    if pose.blink > 0.02:
        ops.append({"op": "lids", "amount": pose.blink, "rgb": METAL_LID})
    return tuple(ops)


def point_in_polygon(x: float, y: float, polygon: list) -> bool:
    """Even-odd test. ``polygon`` points are ``(x, y)`` in the same space as the sample."""
    if len(polygon) < 3:
        return False
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = float(polygon[i][0]), float(polygon[i][1])
        xj, yj = float(polygon[j][0]), float(polygon[j][1])
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def point_in_frame(x: float, y: float, width: float, height: float, outline: list) -> bool:
    """Hit test in AppKit points (origin bottom-left). Outline y grows downward."""
    if width <= 0 or height <= 0:
        return False
    return point_in_polygon(x / width, 1.0 - (y / height), outline)


def write_png(path: Path, width: int, height: int, rgba: bytes) -> None:
    """Uninterlaced 8-bit RGBA PNG. ``rgba`` is row-major, top to bottom."""
    if len(rgba) != width * height * 4:
        raise ValueError("rgba size does not match width and height")
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(rgba[y * stride : (y + 1) * stride])

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def read_png(path: Path) -> tuple[int, int, bytes]:
    """Read an uninterlaced 8-bit RGB or RGBA PNG written by ``write_png`` or similar."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a png")
    pos = 8
    width = height = 0
    color = 0
    idat = bytearray()
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, depth, color, interlace = struct.unpack(">IIBBBBB", chunk)[:5]
            if depth != 8 or color not in (2, 6) or interlace != 0:
                raise ValueError("unsupported png")
        elif tag == b"IDAT":
            idat.extend(chunk)
        elif tag == b"IEND":
            break
    channels = 4 if color == 6 else 3
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray(width * height * 4)
    src = 0
    prev = bytearray(stride)
    for y in range(height):
        filter_type = raw[src]
        src += 1
        row = bytearray(raw[src : src + stride])
        src += stride
        if filter_type == 1:  # Sub
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                row[i] = (row[i] + left) & 255
        elif filter_type == 2:  # Up
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 255
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                row[i] = (row[i] + ((left + prev[i]) // 2)) & 255
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                up = prev[i]
                ul = prev[i - channels] if i >= channels else 0
                row[i] = (row[i] + _paeth(left, up, ul)) & 255
        elif filter_type != 0:
            raise ValueError(f"bad png filter {filter_type}")
        prev = row
        dest = y * width * 4
        if channels == 4:
            out[dest : dest + stride] = row
        else:
            for x in range(width):
                i = x * 3
                j = dest + x * 4
                out[j : j + 3] = row[i : i + 3]
                out[j + 3] = 255
    return width, height, bytes(out)


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    dl = abs(estimate - left)
    du = abs(estimate - up)
    dul = abs(estimate - upper_left)
    if dl <= du and dl <= dul:
        return left
    if du <= dul:
        return up
    return upper_left


def split_robot_eye(rgb: bytes, width: int, height: int) -> tuple[bytes, bytes, dict]:
    """Split a source painting into a frame and a movable iris glow.

    Returns ``(frame_rgba, iris_rgba, measurements)`` at the source size.
    The frame keeps the metal and a dark socket. Pixels outside the frame
    are transparent. The iris image is only the glow (transparent elsewhere).
    """
    if len(rgb) != width * height * 3:
        raise ValueError("rgb size does not match width and height")
    cx, cy = _core_centroid(rgb, width, height)
    rx, ry = _iris_radii(rgb, width, height, cx, cy)
    socket = _socket_color(rgb, width, height, cx, cy, rx, ry)
    frame = bytearray(width * height * 4)
    iris = bytearray(width * height * 4)
    metal = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            r, g, b = _rgb_at(rgb, width, x, y)
            luma = (r + g + b) / 3.0
            blue = b - (r + g) / 2.0
            sat = 0.0 if max(r, g, b) == 0 else (max(r, g, b) - min(r, g, b)) / max(r, g, b)
            if blue < 28 and 42 <= luma <= 248 and sat < 0.28:
                metal[y * width + x] = 1
            nx = (x - cx) / rx
            ny = (y - cy) / ry
            inside = nx * nx + ny * ny <= 1.0
            glow = max(0.0, min(1.0, max((luma - 36.0) / 90.0, blue / 70.0)))
            if inside and (luma > 150 or blue > 16):
                glow = max(glow, 0.85 if luma > 200 else 0.55)
            if inside and glow > 0.08 and not (sat < 0.12 and 70 < luma < 150):
                frame[y * width * 4 + x * 4 : y * width * 4 + x * 4 + 3] = bytes(socket)
                iris_alpha = 255 if glow > 0.22 else int(255 * (glow / 0.22))
                base = y * width * 4 + x * 4
                iris[base : base + 3] = bytes((r, g, b))
                iris[base + 3] = iris_alpha
            else:
                base = y * width * 4 + x * 4
                frame[base : base + 3] = bytes((r, g, b))
            frame[y * width * 4 + x * 4 + 3] = 255
    _punch_background(frame, metal, width, height, radius=5)
    _box_blur_iris(iris, width, height, radius=2)
    return bytes(frame), bytes(iris), {
        "core": [cx, cy],
        "radii": [rx, ry],
        "socket_color": list(socket),
    }


def write_package_assets(
    rgb: bytes,
    width: int,
    height: int,
    dest: Path | None = None,
    *,
    target_width: int = TARGET_WIDTH,
) -> dict:
    """Crop, scale for a small retina overlay, and write frame, iris, and layout."""
    dest = ASSETS if dest is None else dest
    frame, iris, measured = split_robot_eye(rgb, width, height)
    left, top, right, bottom = _alpha_bbox(frame, width, height)
    if right <= left or bottom <= top:
        left, top, right, bottom = 0, 0, width, height
    crop_w = right - left
    crop_h = bottom - top
    scale = target_width / crop_w
    out_w = max(1, int(round(crop_w * scale)))
    out_h = max(1, int(round(crop_h * scale)))
    frame_s = _box_scale(frame, width, height, left, top, crop_w, crop_h, out_w, out_h)
    frame_s = _fill_enclosed(frame_s, out_w, out_h)
    iris_s = _box_scale(iris, width, height, left, top, crop_w, crop_h, out_w, out_h)
    ix0, iy0, ix1, iy1 = _alpha_bbox(iris_s, out_w, out_h, threshold=8)
    pad = 2
    ix0 = max(0, ix0 - pad)
    iy0 = max(0, iy0 - pad)
    ix1 = min(out_w, ix1 + pad)
    iy1 = min(out_h, iy1 + pad)
    iris_w = max(1, ix1 - ix0)
    iris_h = max(1, iy1 - iy0)
    iris_crop = _crop(iris_s, out_w, ix0, iy0, iris_w, iris_h)
    # Socket is the iris ellipse, mapped through the crop and the scale.
    cx, cy = measured["core"]
    rx, ry = measured["radii"]
    sock_cx = (cx - left) * scale / out_w
    sock_cy = (cy - top) * scale / out_h
    sock_rx = (rx * scale) / out_w
    sock_ry = (ry * scale) / out_h
    logical_w = out_w / PIXEL_SCALE
    logical_h = out_h / PIXEL_SCALE
    travel_x = max(6.0, sock_rx * logical_w * 0.28)
    travel_y = max(4.0, sock_ry * logical_h * 0.28)
    outline = _outline(frame_s, out_w, out_h)
    layout = {
        "logical_size": [round(logical_w, 2), round(logical_h, 2)],
        "pixel_size": [out_w, out_h],
        "scale": PIXEL_SCALE,
        "socket": {
            "cx": round(sock_cx, 4),
            "cy": round(sock_cy, 4),
            "rx": round(sock_rx, 4),
            "ry": round(sock_ry, 4),
        },
        "iris": {
            "x": round(ix0 / out_w, 4),
            "y": round(iy0 / out_h, 4),
            "w": round(iris_w / out_w, 4),
            "h": round(iris_h / out_h, 4),
        },
        "travel": {"x": round(travel_x, 2), "y": round(travel_y, 2)},
        "outline": outline,
        "socket_color": measured["socket_color"],
    }
    dest.mkdir(parents=True, exist_ok=True)
    write_png(dest / "frame.png", out_w, out_h, frame_s)
    write_png(dest / "iris.png", iris_w, iris_h, iris_crop)
    (dest / "layout.json").write_text(json.dumps(layout, indent=2) + "\n", encoding="utf-8")
    reset_layout_cache()
    return layout


def _rgb_at(rgb: bytes, width: int, x: int, y: int) -> tuple[int, int, int]:
    i = (y * width + x) * 3
    return rgb[i], rgb[i + 1], rgb[i + 2]


def _core_centroid(rgb: bytes, width: int, height: int) -> tuple[float, float]:
    x0, x1 = width // 5, width * 4 // 5
    y0, y1 = height // 5, height * 4 // 5
    sx = sy = n = 0
    bright = 0
    bx = by = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            r, g, b = _rgb_at(rgb, width, x, y)
            luma = r + g + b
            if luma > bright:
                bright = luma
                bx, by = x, y
            if r > 232 and g > 232 and b > 232:
                sx += x
                sy += y
                n += 1
    if n >= 8:
        return sx / n, sy / n
    return float(bx), float(by)


def _iris_radii(rgb: bytes, width: int, height: int, cx: float, cy: float) -> tuple[float, float]:
    def walk(dx: int, dy: int) -> int:
        last = 3
        limit = max(width, height)
        for step in range(1, limit):
            x = int(round(cx + dx * step))
            y = int(round(cy + dy * step))
            if x < 0 or y < 0 or x >= width or y >= height:
                return last
            r, g, b = _rgb_at(rgb, width, x, y)
            luma = (r + g + b) / 3.0
            blue = b - (r + g) / 2.0
            if luma < 72 and blue < 22:
                return max(3, step - 1)
            last = step
        return last

    rx = max(walk(1, 0), walk(-1, 0))
    ry = max(walk(0, 1), walk(0, -1))
    rx = max(4.0, rx * 1.06 + 1)
    ry = max(3.0, ry * 1.12 + 1)
    return rx, ry


def _socket_color(
    rgb: bytes, width: int, height: int, cx: float, cy: float, rx: float, ry: float
) -> tuple[int, int, int]:
    """Dark navy sampled just outside the glow, inside the socket."""
    sr = sg = sb = n = 0
    for y in range(int(cy - ry * 1.6), int(cy + ry * 1.6) + 1):
        if y < 0 or y >= height:
            continue
        for x in range(int(cx - rx * 1.5), int(cx + rx * 1.5) + 1):
            if x < 0 or x >= width:
                continue
            nx = (x - cx) / rx
            ny = (y - cy) / ry
            dist = nx * nx + ny * ny
            if dist < 1.05 or dist > 2.4:
                continue
            r, g, b = _rgb_at(rgb, width, x, y)
            if (r + g + b) / 3.0 > 48:
                continue
            sr += r
            sg += g
            sb += b
            n += 1
    if n < 8:
        return (6, 8, 16)
    return (max(0, min(32, sr // n)), max(0, min(32, sg // n)), max(0, min(40, sb // n)))


def _dilate(mask: bytearray, width: int, height: int, radius: int) -> bytearray:
    out = bytearray(width * height)
    for y in range(height):
        y0 = max(0, y - radius)
        y1 = min(height - 1, y + radius)
        for x in range(width):
            x0 = max(0, x - radius)
            x1 = min(width - 1, x + radius)
            hit = 0
            for yy in range(y0, y1 + 1):
                row = yy * width
                for xx in range(x0, x1 + 1):
                    if mask[row + xx]:
                        hit = 1
                        break
                if hit:
                    break
            out[y * width + x] = hit
    return out


def _punch_background(
    frame: bytearray, metal: bytearray, width: int, height: int, radius: int = 5
) -> None:
    """Transparent outside the closed metal silhouette.

    The dilate seals brushed-metal grooves so the flood does not eat the frame.
    On a small image the radius shrinks so a test pattern's margin stays clear.
    """
    radius = max(1, min(int(radius), max(1, width // 50)))
    closed = _dilate(metal, width, height, radius)
    seen = bytearray(width * height)
    stack: list[tuple[int, int]] = []
    for x in range(width):
        for y in (0, height - 1):
            i = y * width + x
            if not closed[i] and not seen[i]:
                seen[i] = 1
                stack.append((x, y))
    for y in range(height):
        for x in (0, width - 1):
            i = y * width + x
            if not closed[i] and not seen[i]:
                seen[i] = 1
                stack.append((x, y))
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            i = ny * width + nx
            if seen[i] or closed[i]:
                continue
            seen[i] = 1
            stack.append((nx, ny))
    for i, flag in enumerate(seen):
        if flag:
            frame[i * 4 + 3] = 0


def _box_blur_iris(iris: bytearray, width: int, height: int, radius: int) -> None:
    """Soften baked dashes. Procedural rings replace them and can rotate."""
    if radius <= 0:
        return
    src = bytes(iris)
    for y in range(height):
        y0 = max(0, y - radius)
        y1 = min(height - 1, y + radius)
        for x in range(width):
            base = (y * width + x) * 4
            if src[base + 3] == 0:
                continue
            x0 = max(0, x - radius)
            x1 = min(width - 1, x + radius)
            sr = sg = sb = sa = n = 0
            for yy in range(y0, y1 + 1):
                row = yy * width * 4
                for xx in range(x0, x1 + 1):
                    i = row + xx * 4
                    a = src[i + 3]
                    if a == 0:
                        continue
                    sr += src[i]
                    sg += src[i + 1]
                    sb += src[i + 2]
                    sa += a
                    n += 1
            if n == 0:
                continue
            iris[base] = sr // n
            iris[base + 1] = sg // n
            iris[base + 2] = sb // n
            iris[base + 3] = sa // n


def _alpha_bbox(rgba: bytes, width: int, height: int, threshold: int = 16) -> tuple[int, int, int, int]:
    minx, miny, maxx, maxy = width, height, 0, 0
    for y in range(height):
        row = y * width * 4
        for x in range(width):
            if rgba[row + x * 4 + 3] > threshold:
                if x < minx:
                    minx = x
                if y < miny:
                    miny = y
                if x + 1 > maxx:
                    maxx = x + 1
                if y + 1 > maxy:
                    maxy = y + 1
    if maxx <= minx or maxy <= miny:
        return 0, 0, width, height
    return minx, miny, maxx, maxy


def _crop(rgba: bytes, width: int, x: int, y: int, crop_w: int, crop_h: int) -> bytes:
    out = bytearray(crop_w * crop_h * 4)
    for row in range(crop_h):
        src = ((y + row) * width + x) * 4
        dest = row * crop_w * 4
        out[dest : dest + crop_w * 4] = rgba[src : src + crop_w * 4]
    return bytes(out)


def _box_scale(
    rgba: bytes,
    width: int,
    height: int,
    left: int,
    top: int,
    crop_w: int,
    crop_h: int,
    out_w: int,
    out_h: int,
) -> bytes:
    out = bytearray(out_w * out_h * 4)
    for y in range(out_h):
        sy0 = top + int(y * crop_h / out_h)
        sy1 = top + int((y + 1) * crop_h / out_h)
        if sy1 <= sy0:
            sy1 = sy0 + 1
        sy1 = min(height, sy1)
        for x in range(out_w):
            sx0 = left + int(x * crop_w / out_w)
            sx1 = left + int((x + 1) * crop_w / out_w)
            if sx1 <= sx0:
                sx1 = sx0 + 1
            sx1 = min(width, sx1)
            sr = sg = sb = sa = n = 0
            for yy in range(sy0, sy1):
                row = yy * width * 4
                for xx in range(sx0, sx1):
                    i = row + xx * 4
                    a = rgba[i + 3]
                    sr += rgba[i] * a
                    sg += rgba[i + 1] * a
                    sb += rgba[i + 2] * a
                    sa += a
                    n += 1
            dest = (y * out_w + x) * 4
            if sa == 0 or n == 0:
                continue
            out[dest] = sr // sa
            out[dest + 1] = sg // sa
            out[dest + 2] = sb // sa
            out[dest + 3] = sa // n
    return bytes(out)


def _fill_enclosed(rgba: bytes, width: int, height: int) -> bytes:
    """Fill transparent pockets that downscaling pinched off from the border."""
    out = bytearray(rgba)
    seen = bytearray(width * height)
    stack: list[tuple[int, int]] = []
    for x in range(width):
        for y in (0, height - 1):
            i = y * width + x
            if out[i * 4 + 3] == 0 and not seen[i]:
                seen[i] = 1
                stack.append((x, y))
    for y in range(height):
        for x in (0, width - 1):
            i = y * width + x
            if out[i * 4 + 3] == 0 and not seen[i]:
                seen[i] = 1
                stack.append((x, y))
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            i = ny * width + nx
            if seen[i] or out[i * 4 + 3] != 0:
                continue
            seen[i] = 1
            stack.append((nx, ny))
    for y in range(height):
        for x in range(width):
            i = y * width + x
            if out[i * 4 + 3] != 0 or seen[i]:
                continue
            sr = sg = sb = n = 0
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    nx, ny = x + dx, y + dy
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    j = (ny * width + nx) * 4
                    if out[j + 3] == 0:
                        continue
                    sr += out[j]
                    sg += out[j + 1]
                    sb += out[j + 2]
                    n += 1
            base = i * 4
            if n:
                out[base] = sr // n
                out[base + 1] = sg // n
                out[base + 2] = sb // n
            out[base + 3] = 255
    return bytes(out)


def _outline(rgba: bytes, width: int, height: int) -> list[list[float]]:
    """A coarse silhouette, image y downward, normalized."""
    cx = width / 2.0
    cy = height / 2.0
    points: list[list[float]] = []
    steps = 20
    limit = int(math.hypot(width, height))
    for i in range(steps):
        angle = -math.pi / 2 + i * (2 * math.pi / steps)
        last_x = last_y = None
        for dist in range(0, limit):
            x = int(round(cx + math.cos(angle) * dist))
            y = int(round(cy + math.sin(angle) * dist))
            if x < 0 or y < 0 or x >= width or y >= height:
                break
            if rgba[(y * width + x) * 4 + 3] > 32:
                last_x, last_y = x, y
            elif last_x is not None and dist > 6:
                break
        if last_x is None:
            continue
        points.append([round(last_x / width, 4), round(last_y / height, 4)])
    if len(points) < 6:
        return _default_layout()["outline"]
    return points
