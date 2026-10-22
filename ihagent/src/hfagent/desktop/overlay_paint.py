"""Paint a draw list with AppKit or Qt. Imported only by the overlay process."""

from __future__ import annotations


def _alpha(color: tuple, opacity: float) -> tuple[float, float, float, float]:
    red, green, blue = color[0], color[1], color[2]
    alpha = color[3] if len(color) > 3 else 1.0
    return (float(red), float(green), float(blue), max(0.0, min(1.0, float(alpha) * opacity)))


def paint_appkit(ops: list[dict], opacity: float) -> None:
    """Draw into the current AppKit context. The view is flipped (y down).

    One bad op is skipped. A text failure used to abort the rest of the
    frame, which hid the arrow whenever a label was in the list.
    """
    from AppKit import (
        NSBezierPath,
        NSColor,
        NSFont,
        NSFontAttributeName,
        NSForegroundColorAttributeName,
        NSMakeRect,
    )
    from Foundation import NSString

    def color_of(value: tuple):
        red, green, blue, alpha = _alpha(value, opacity)
        return NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, alpha)

    for op in ops:
        try:
            _paint_appkit_op(
                op,
                color_of,
                NSBezierPath,
                NSFont,
                NSFontAttributeName,
                NSForegroundColorAttributeName,
                NSMakeRect,
                NSString,
            )
        except Exception:
            continue


def _paint_appkit_op(
    op,
    color_of,
    NSBezierPath,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSString,
) -> None:
    kind = op.get("op")
    if kind == "ellipse":
        rect = NSMakeRect(
            float(op["cx"]) - float(op["rx"]),
            float(op["cy"]) - float(op["ry"]),
            float(op["rx"]) * 2.0,
            float(op["ry"]) * 2.0,
        )
        path = NSBezierPath.bezierPathWithOvalInRect_(rect)
        if op.get("fill") is not None:
            color_of(op["fill"]).setFill()
            path.fill()
        if op.get("stroke") is not None:
            color_of(op["stroke"]).setStroke()
            path.setLineWidth_(float(op.get("sw") or 1.0))
            path.stroke()
    elif kind == "roundrect":
        rect = NSMakeRect(float(op["x"]), float(op["y"]), float(op["w"]), float(op["h"]))
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, float(op.get("r") or 0.0), float(op.get("r") or 0.0)
        )
        if op.get("fill") is not None:
            color_of(op["fill"]).setFill()
            path.fill()
        if op.get("stroke") is not None:
            color_of(op["stroke"]).setStroke()
            path.setLineWidth_(float(op.get("sw") or 1.0))
            path.stroke()
    elif kind == "line":
        if op.get("stroke") is None:
            return
        path = NSBezierPath.alloc().init()
        path.moveToPoint_((float(op["x1"]), float(op["y1"])))
        path.lineToPoint_((float(op["x2"]), float(op["y2"])))
        color_of(op["stroke"]).setStroke()
        path.setLineWidth_(float(op.get("sw") or 1.0))
        path.stroke()
    elif kind == "polygon":
        points = list(op.get("points") or [])
        if len(points) < 3:
            return
        path = NSBezierPath.alloc().init()
        path.moveToPoint_((float(points[0][0]), float(points[0][1])))
        for px, py in points[1:]:
            path.lineToPoint_((float(px), float(py)))
        path.closePath()
        if op.get("fill") is not None:
            color_of(op["fill"]).setFill()
            path.fill()
        if op.get("stroke") is not None:
            color_of(op["stroke"]).setStroke()
            path.setLineWidth_(float(op.get("sw") or 1.0))
            path.stroke()
    elif kind == "text":
        fill = op.get("fill") or (1.0, 1.0, 1.0, 1.0)
        size = float(op.get("size") or 12.0)
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(size),
            NSForegroundColorAttributeName: color_of(fill),
        }
        NSString.stringWithString_(str(op.get("text") or "")).drawAtPoint_withAttributes_(
            (float(op["x"]), float(op["y"])), attrs
        )


def paint_qt(painter, ops: list[dict], opacity: float) -> None:
    """Draw with a PySide6 ``QPainter``. Coordinates are logical pixels, y down."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPen

    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    font = QFont()
    font.setPointSize(12)
    painter.setFont(font)

    def qcolor(value: tuple) -> QColor:
        red, green, blue, alpha = _alpha(value, opacity)
        return QColor.fromRgbF(red, green, blue, alpha)

    for op in ops:
        try:
            _paint_qt_op(painter, op, qcolor, Qt, QPen)
        except Exception:
            continue


def _paint_qt_op(painter, op, qcolor, Qt, QPen) -> None:
    kind = op.get("op")
    if kind == "ellipse":
        painter.setPen(QPen(qcolor(op["stroke"]), float(op.get("sw") or 1.0)) if op.get("stroke") else Qt.PenStyle.NoPen)
        painter.setBrush(qcolor(op["fill"]) if op.get("fill") is not None else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(
            int(round(float(op["cx"]) - float(op["rx"]))),
            int(round(float(op["cy"]) - float(op["ry"]))),
            int(round(float(op["rx"]) * 2.0)),
            int(round(float(op["ry"]) * 2.0)),
        )
    elif kind == "roundrect":
        painter.setPen(QPen(qcolor(op["stroke"]), float(op.get("sw") or 1.0)) if op.get("stroke") else Qt.PenStyle.NoPen)
        painter.setBrush(qcolor(op["fill"]) if op.get("fill") is not None else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(
            int(round(float(op["x"]))),
            int(round(float(op["y"]))),
            int(round(float(op["w"]))),
            int(round(float(op["h"]))),
            float(op.get("r") or 0.0),
            float(op.get("r") or 0.0),
        )
    elif kind == "line" and op.get("stroke") is not None:
        painter.setPen(QPen(qcolor(op["stroke"]), float(op.get("sw") or 1.0)))
        painter.drawLine(
            int(round(float(op["x1"]))),
            int(round(float(op["y1"]))),
            int(round(float(op["x2"]))),
            int(round(float(op["y2"]))),
        )
    elif kind == "polygon":
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPolygonF

        points = list(op.get("points") or [])
        if len(points) < 3:
            return
        painter.setPen(QPen(qcolor(op["stroke"]), float(op.get("sw") or 1.0)) if op.get("stroke") else Qt.PenStyle.NoPen)
        painter.setBrush(qcolor(op["fill"]) if op.get("fill") is not None else Qt.BrushStyle.NoBrush)
        painter.drawPolygon(QPolygonF([QPointF(float(px), float(py)) for px, py in points]))
    elif kind == "text":
        size = float(op.get("size") or 12.0)
        painter.setPen(QPen(qcolor(op.get("fill") or (1, 1, 1, 1))))
        painter.drawText(
            int(round(float(op["x"]))),
            int(round(float(op["y"]) + size)),
            str(op.get("text") or ""),
        )
