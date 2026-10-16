"""Paint iheye skins with QPainter.

Bot and classic follow the macOS draw list (``describe_bot`` and the classic
``drawRect_`` geometry). The grok face uses ``describe_face``. The brushed-metal
robot draws the same frame and iris assets, then the rings from
``describe_frame``. If those assets are missing, the robot skin paints the bot.

Coordinates inside the draw lists grow upward. This module flips them into
Qt's top-left origin. PySide6 is imported here, not by the supervisor.
"""

from __future__ import annotations

import math

from .appearance import eye_geometry
from .bot import bot_pose, describe_bot
from .face import describe_face, face_pose
from .robot import ASSETS, describe_frame, load_layout, normalize_skin, panel_size, robot_pose


def _qt():
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap

    return QPointF, Qt, QColor, QImage, QPainter, QPainterPath, QPen, QPixmap


def _color(rgb, alpha: float):
    _, _, QColor, _, _, _, _, _ = _qt()
    r, g, b = rgb
    return QColor.fromRgbF(
        max(0.0, min(1.0, float(r))),
        max(0.0, min(1.0, float(g))),
        max(0.0, min(1.0, float(b))),
        max(0.0, min(1.0, float(alpha))),
    )


def _flip(y: float, height: float) -> float:
    return float(height) - float(y)


def paint_ops(painter, ops, height: float) -> None:
    """Draw bot-style ops. ``y`` in each op grows upward."""
    QPointF, Qt, _, _, _, QPainterPath, QPen, _ = _qt()
    for op in ops:
        kind = op.get("op")
        rgb = op.get("rgb") or (1.0, 1.0, 1.0)
        alpha = float(op.get("alpha", 1.0))
        color = _color(rgb, alpha)
        clip = op.get("clip")
        if clip:
            painter.save()
            path = QPainterPath()
            if "r" in clip:
                radius = float(clip["r"])
                path.addEllipse(
                    float(clip["cx"]) - radius,
                    _flip(float(clip["cy"]) + radius, height),
                    radius * 2.0,
                    radius * 2.0,
                )
            else:
                rx, ry = float(clip["rx"]), float(clip["ry"])
                path.addEllipse(
                    float(clip["cx"]) - rx,
                    _flip(float(clip["cy"]) + ry, height),
                    rx * 2.0,
                    ry * 2.0,
                )
            painter.setClipPath(path)
        if kind in {"oval", "stroke", "roundrect"}:
            if kind == "stroke":
                pen = QPen(color)
                pen.setWidthF(max(0.6, float(op.get("width") or 1.0)))
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
        if kind == "oval":
            rx, ry = float(op["rx"]), float(op["ry"])
            painter.drawEllipse(
                float(op["cx"]) - rx,
                _flip(float(op["cy"]) + ry, height),
                rx * 2.0,
                ry * 2.0,
            )
        elif kind == "stroke":
            rx, ry = float(op["rx"]), float(op["ry"])
            painter.drawEllipse(
                float(op["cx"]) - rx,
                _flip(float(op["cy"]) + ry, height),
                rx * 2.0,
                ry * 2.0,
            )
        elif kind == "roundrect":
            painter.drawRoundedRect(
                float(op["x"]),
                _flip(float(op["y"]) + float(op["h"]), height),
                float(op["w"]),
                float(op["h"]),
                float(op.get("radius") or 0.0),
                float(op.get("radius") or 0.0),
            )
        elif kind in {"limb", "poly"}:
            pen = QPen(color)
            pen.setWidthF(max(0.6, float(op.get("width") or 2.0)))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if kind == "limb":
                painter.drawLine(
                    QPointF(float(op["x0"]), _flip(float(op["y0"]), height)),
                    QPointF(float(op["x1"]), _flip(float(op["y1"]), height)),
                )
            else:
                pts = op.get("points") or []
                if len(pts) >= 2:
                    path = QPainterPath()
                    path.moveTo(float(pts[0][0]), _flip(float(pts[0][1]), height))
                    for px, py in pts[1:]:
                        path.lineTo(float(px), _flip(float(py), height))
                    painter.strokePath(path, pen)
        if clip:
            painter.restore()


def paint_bot(
    painter,
    width: float,
    height: float,
    *,
    state: str = "idle",
    phase: float = 0.0,
    pupil=None,
    blink_extra: float = 0.0,
    speaking: bool = False,
    working: bool = False,
    failed: bool = False,
    reduce_motion: bool = False,
) -> None:
    pose = bot_pose(
        state,
        phase,
        pupil=pupil,
        blink_extra=blink_extra,
        speaking=speaking,
        working=working,
        failed=failed,
        reduce_motion=reduce_motion,
    )
    paint_ops(painter, describe_bot(pose, width, height), height)


def paint_classic(
    painter,
    width: float,
    height: float,
    *,
    state: str = "idle",
    phase: float = 0.0,
    pupil=None,
    blink_extra: float = 0.0,
    **_ignored,
) -> None:
    """The original rounded bubble. Same geometry as the macOS ``drawRect_``."""
    _, Qt, _, _, _, QPainterPath, QPen, _ = _qt()
    geo = eye_geometry(
        state,
        phase,
        cx=width / 2.0,
        cy=58.0 if abs(height - 100.0) < 1.0 else height * 0.58,
        pupil_override=pupil,
        blink_extra=blink_extra,
    )
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_color((0.06, 0.09, 0.14), 0.94))
    painter.drawRoundedRect(4, 4, width - 8, height - 8, 22, 22)
    if geo.pulse > 0.02:
        pen = QPen(_color(geo.glow_rgb, min(0.95, geo.pulse)))
        pen.setWidthF(3.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(2, 2, width - 4, height - 4, 24, 24)

    mid_y = (geo.top + geo.bottom) / 2.0
    mid_x = (geo.left + geo.right) / 2.0
    eye = QPainterPath()
    eye.moveTo(geo.left, _flip(mid_y, height))
    eye.cubicTo(
        mid_x,
        _flip(geo.top, height),
        mid_x,
        _flip(geo.top, height),
        geo.right,
        _flip(mid_y, height),
    )
    eye.cubicTo(
        mid_x,
        _flip(geo.bottom, height),
        mid_x,
        _flip(geo.bottom, height),
        geo.left,
        _flip(mid_y, height),
    )
    eye.closeSubpath()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_color((0.96, 0.96, 0.96), 1.0))
    painter.drawPath(eye)
    pen = QPen(_color(geo.iris_rgb, 1.0))
    pen.setWidthF(1.4)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(eye)

    def oval(cx, cy, radius, rgb, alpha):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_color(rgb, alpha))
        painter.drawEllipse(
            cx - radius,
            _flip(cy + radius, height),
            radius * 2.0,
            radius * 2.0,
        )

    icx, icy, ir = geo.iris
    oval(icx, icy, ir, geo.iris_rgb, 1.0)
    pcx, pcy, pr = geo.pupil
    oval(pcx, pcy, pr, (0.05, 0.07, 0.10), 1.0)
    hx, hy, hr = geo.highlight
    oval(hx, hy, hr, (1.0, 1.0, 1.0), 0.95)

    painter.setPen(_color((0.75, 0.93, 0.90), 1.0))
    painter.drawText(8, int(height - 28), int(width - 16), 16, int(Qt.AlignmentFlag.AlignCenter), "iheye")


def paint_grok(
    painter,
    width: float,
    height: float,
    *,
    state: str = "idle",
    phase: float = 0.0,
    pupil=None,
    blink_extra: float = 0.0,
    speaking: bool = False,
    **_ignored,
) -> None:
    _, Qt, _, _, _, QPainterPath, QPen, _ = _qt()
    pose = face_pose(
        state,
        phase,
        pupil=pupil,
        blink_extra=blink_extra,
        speaking=speaking,
    )
    for op in describe_face(pose, width, height):
        kind = op.get("op")
        rgb = op.get("rgb") or (1, 1, 1)
        alpha = float(op.get("alpha", 1.0))
        color = _color(rgb, alpha)
        clip_r = op.get("clip_r")
        if clip_r:
            painter.save()
            clip = QPainterPath()
            cx, cy = op.get("clip") or (width / 2.0, height / 2.0)
            radius = float(clip_r)
            clip.addEllipse(cx - radius, _flip(cy + radius, height), radius * 2.0, radius * 2.0)
            painter.setClipPath(clip)
        if kind in {"disc", "shade", "sheen", "eye", "pupil", "catch", "mouth_blob"}:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            if kind in {"disc", "pupil", "catch"}:
                radius = float(op["r"])
                painter.drawEllipse(
                    float(op["cx"]) - radius,
                    _flip(float(op["cy"]) + radius, height),
                    radius * 2.0,
                    radius * 2.0,
                )
            else:
                rx, ry = float(op["rx"]), float(op["ry"])
                painter.drawEllipse(
                    float(op["cx"]) - rx,
                    _flip(float(op["cy"]) + ry, height),
                    rx * 2.0,
                    ry * 2.0,
                )
        elif kind == "rim":
            pen = QPen(color)
            pen.setWidthF(float(op.get("width") or 2.0))
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            radius = float(op["r"])
            painter.drawEllipse(
                float(op["cx"]) - radius,
                _flip(float(op["cy"]) + radius, height),
                radius * 2.0,
                radius * 2.0,
            )
        elif kind == "mouth_arc":
            pen = QPen(color)
            pen.setWidthF(float(op.get("width") or 3.0))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            radius = float(op["radius"])
            # AppKit degrees, y-up. Negate so a smile stays a smile in Qt.
            start = -float(op["end"])
            span = float(op["end"]) - float(op["start"])
            rect_y = _flip(float(op["cy"]) + radius, height)
            painter.drawArc(
                float(op["cx"]) - radius,
                rect_y,
                radius * 2.0,
                radius * 2.0,
                int(start * 16),
                int(span * 16),
            )
        elif kind == "mouth_line":
            pen = QPen(color)
            pen.setWidthF(float(op.get("width") or 3.0))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(float(op["x0"]), _flip(float(op["y0"]), height))
            path.lineTo(float(op["x1"]), _flip(float(op["y1"]), height))
            path.lineTo(float(op["x2"]), _flip(float(op["y2"]), height))
            painter.strokePath(path, pen)
        if clip_r:
            painter.restore()


def paint_robot(
    painter,
    width: float,
    height: float,
    *,
    state: str = "idle",
    phase: float = 0.0,
    pupil=None,
    blink_extra: float = 0.0,
    **_ignored,
) -> bool:
    """Brushed-metal eye. False when the frame or iris asset is missing."""
    _, Qt, _, _, _, QPainterPath, QPen, QPixmap = _qt()
    frame_path = ASSETS / "frame.png"
    iris_path = ASSETS / "iris.png"
    if not frame_path.is_file() or not iris_path.is_file():
        return False
    frame = QPixmap(str(frame_path))
    iris = QPixmap(str(iris_path))
    if frame.isNull() or iris.isNull():
        return False
    layout = load_layout()
    pose = robot_pose(state, phase, pupil=pupil, blink_extra=blink_extra, layout=layout)
    painter.drawPixmap(0, 0, int(width), int(height), frame)

    socket = layout.get("socket") or {}
    home = layout.get("iris") or {}
    cx = width * float(socket.get("cx", 0.5))
    cy = height * float(socket.get("cy", 0.5))
    rx = width * float(socket.get("rx", 0.24))
    ry = height * float(socket.get("ry", 0.16))
    ix = width * float(home.get("x", 0.32)) + float(pose.iris_dx)
    iy = height * float(home.get("y", 0.36)) - float(pose.iris_dy)
    iw = width * float(home.get("w", 0.36))
    ih = height * float(home.get("h", 0.28))

    painter.save()
    clip = QPainterPath()
    clip.addEllipse(cx - rx, cy - ry, rx * 2.0, ry * 2.0)
    painter.setClipPath(clip)
    painter.setOpacity(min(1.0, float(pose.glow)))
    painter.drawPixmap(int(ix), int(iy), int(iw), int(ih), iris)
    painter.setOpacity(1.0)
    center_x = ix + iw / 2.0
    center_y = iy + ih / 2.0
    minor = max(8.0, min(iw, ih))
    for op in describe_frame(pose):
        if op["op"] == "ring":
            _paint_ring(painter, op, center_x, center_y, minor)
        elif op["op"] == "core":
            radius = minor * 0.22 * float(op["scale"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_color((1, 1, 1), float(op["alpha"])))
            painter.drawEllipse(center_x - radius, center_y - radius, radius * 2.0, radius * 2.0)
        elif op["op"] == "tint":
            rgb = op["rgb"]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_color(rgb, float(op["alpha"]) * 0.55))
            painter.drawEllipse(
                center_x - minor * 0.55,
                center_y - minor * 0.4,
                minor * 1.1,
                minor * 0.8,
            )
        elif op["op"] == "lids":
            cover = (ry * 2.0) * float(op["amount"]) * 0.5
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_color(op["rgb"], 0.96))
            painter.drawRect(cx - rx, cy - ry, rx * 2.0, cover)
            painter.drawRect(cx - rx, cy + ry - cover, rx * 2.0, cover)
    painter.restore()
    return True


def _paint_ring(painter, op, cx: float, cy: float, side: float) -> None:
    _, Qt, _, _, _, QPainterPath, QPen, _ = _qt()
    rgba = op["rgba"]
    color = _color(rgba[:3], rgba[3])
    pen = QPen(color)
    pen.setWidthF(max(0.8, float(op.get("width") or 1.2)))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    radius = side * 0.5 * float(op["radius"])
    span = math.tau / max(1, int(op["dashes"]))
    duty = float(op["duty"])
    rotation = float(op["rotation"])
    for index in range(int(op["dashes"])):
        a0 = rotation + index * span
        a1 = a0 + span * duty
        path = QPainterPath()
        steps = 4
        for step in range(steps + 1):
            angle = a0 + (a1 - a0) * step / steps
            px = cx + math.cos(angle) * radius
            py = cy + math.sin(angle) * radius
            if step == 0:
                path.moveTo(px, py)
            else:
                path.lineTo(px, py)
        painter.strokePath(path, pen)


def paint_skin(painter, skin: str, width: float, height: float, **pose) -> str:
    """Paint ``skin`` and return the skin that was actually drawn."""
    name = normalize_skin(skin)
    if name == "classic":
        paint_classic(painter, width, height, **pose)
        return "classic"
    if name == "grok":
        paint_grok(painter, width, height, **pose)
        return "grok"
    if name == "robot":
        if paint_robot(painter, width, height, **pose):
            return "robot"
        paint_bot(painter, width, height, **pose)
        return "bot"
    paint_bot(painter, width, height, **pose)
    return "bot"


def render_skin_image(
    skin: str,
    *,
    state: str = "idle",
    phase: float = 1.1,
    working: bool = False,
    failed: bool = False,
    speaking: bool = False,
    reduce_motion: bool = False,
    scale: int = 2,
):
    """Rasterize one skin to a ``QImage``. Creates an offscreen app if needed."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _, Qt, _, QImage, QPainter, _, _, _ = _qt()
    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is None:
        QApplication(["iheye-qt"])
    name = normalize_skin(skin)
    width, height = panel_size(name)
    scale = max(1, int(scale))
    image = QImage(int(width * scale), int(height * scale), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(scale, scale)
    paint_skin(
        painter,
        skin,
        width,
        height,
        state=state,
        phase=phase,
        working=working,
        failed=failed,
        speaking=speaking,
        reduce_motion=reduce_motion,
    )
    painter.end()
    return image


def save_skin_png(path: str, skin: str, **kwargs) -> str:
    image = render_skin_image(skin, **kwargs)
    if not image.save(path, "PNG"):
        raise OSError(f"could not write {path}")
    return path
