"""Draw list for the ihmouse pointer. No GUI imports.

The cursor is a glowing arrow, not the system pointer: white fill, cyan
edge, cyan halo. A small IH badge with the iheye face (white body, black
face, cyan eyes) sits off the tip so the click point stays visible.
Coordinates are pointer space, y down. The hotspot is the arrow tip.
"""

from __future__ import annotations

import math

from .geometry import Rect
from .overlay_geom import label_box
from .overlay_motion import Frame


CYAN = (0.22, 0.84, 1.0, 1.0)
WHITE = (0.97, 0.98, 1.0, 1.0)
BLACK = (0.06, 0.07, 0.09, 1.0)
METAL = (0.55, 0.62, 0.72, 1.0)
INK = (0.85, 0.97, 1.0, 1.0)
# Right-click ripple. Far enough from cyan that it is not a left click.
RIGHT = (1.0, 0.42, 0.55, 1.0)

# Tip at the hotspot. The body runs down-right so the target stays clear.
_ARROW = (
    (0.0, 0.0),
    (0.0, 22.0),
    (6.2, 16.2),
    (11.5, 27.5),
    (17.2, 23.8),
    (11.2, 13.0),
    (20.5, 11.5),
)


def _scale_op(op: dict, scale: float) -> dict:
    if scale == 1.0:
        return op
    item = dict(op)
    for key in ("x", "cx", "x1", "x2", "y", "cy", "y1", "y2", "rx", "ry", "w", "h", "sw", "r"):
        if key in item:
            item[key] = float(item[key]) * scale
    if "points" in item:
        item["points"] = [(float(px) * scale, float(py) * scale) for px, py in item["points"]]
    if "size" in item:
        item["size"] = float(item["size"]) * scale
    return item


def describe_cursor(phase: float) -> list[dict]:
    """Arrow and IH badge in a local box whose origin is the click point."""
    glow = 0.22 + 0.14 * (0.5 + 0.5 * math.sin(phase * 3.0))
    return [
        {
            "part": "ring",
            "op": "ellipse",
            "cx": 12.0,
            "cy": 16.0,
            "rx": 26.0,
            "ry": 26.0,
            "fill": (CYAN[0], CYAN[1], CYAN[2], glow * 0.28),
            "stroke": CYAN,
            "sw": 2.0,
        },
        {
            "part": "glow",
            "op": "ellipse",
            "cx": 12.0,
            "cy": 16.0,
            "rx": 34.0,
            "ry": 34.0,
            "fill": (CYAN[0], CYAN[1], CYAN[2], glow * 0.12),
        },
        {
            "part": "arrow",
            "op": "polygon",
            "points": _ARROW,
            "fill": WHITE,
            "stroke": CYAN,
            "sw": 1.8,
        },
        {
            "part": "body",
            "op": "ellipse",
            "cx": 38.0,
            "cy": 36.0,
            "rx": 12.0,
            "ry": 11.0,
            "fill": WHITE,
            "stroke": METAL,
            "sw": 1.0,
        },
        {
            "part": "face",
            "op": "roundrect",
            "x": 30.5,
            "y": 31.0,
            "w": 15.0,
            "h": 8.5,
            "r": 3.0,
            "fill": BLACK,
        },
        {
            "part": "eye",
            "op": "ellipse",
            "cx": 34.2,
            "cy": 35.0,
            "rx": 1.6,
            "ry": 1.6,
            "fill": CYAN,
        },
        {
            "part": "eye",
            "op": "ellipse",
            "cx": 41.6,
            "cy": 35.0,
            "rx": 1.6,
            "ry": 1.6,
            "fill": CYAN,
        },
        {
            "part": "badge",
            "op": "text",
            "text": "IH",
            "x": 32.4,
            "y": 42.5,
            "fill": CYAN,
            "size": 8.0,
        },
        {
            "part": "hotspot",
            "op": "ellipse",
            "cx": 0.0,
            "cy": 0.0,
            "rx": 2.6,
            "ry": 2.6,
            "fill": CYAN,
            "stroke": WHITE,
            "sw": 0.8,
        },
    ]


def _shift(op: dict, dx: float, dy: float) -> dict:
    item = dict(op)
    for key in ("x", "cx", "x1", "x2"):
        if key in item:
            item[key] = float(item[key]) + dx
    for key in ("y", "cy", "y1", "y2"):
        if key in item:
            item[key] = float(item[key]) + dy
    if "points" in item:
        item["points"] = [(float(px) + dx, float(py) + dy) for px, py in item["points"]]
    return item


def describe_scene(frame: Frame, virtual: Rect | None = None) -> list[dict]:
    """Highlight, label, pulse or typing dots, then the robot. Pointer space."""
    if not frame.visible or frame.opacity <= 0.0:
        return []
    ops: list[dict] = []
    if frame.bounds is not None and frame.bounds.width > 0 and frame.bounds.height > 0:
        box = frame.bounds
        ops.append(
            {
                "part": "highlight",
                "op": "roundrect",
                "x": box.x,
                "y": box.y,
                "w": box.width,
                "h": box.height,
                "r": 8.0,
                "fill": (CYAN[0], CYAN[1], CYAN[2], 0.14),
                "stroke": CYAN,
                "sw": 2.0,
            }
        )
        if frame.label and frame.show_label:
            pill = label_box(box, frame.label, virtual)
            ops.append(
                {
                    "part": "label",
                    "op": "roundrect",
                    "x": pill.x,
                    "y": pill.y,
                    "w": pill.width,
                    "h": pill.height,
                    "r": 8.0,
                    "fill": (0.05, 0.08, 0.12, 0.9),
                }
            )
            ops.append(
                {
                    "part": "label",
                    "op": "text",
                    "text": frame.label,
                    "x": pill.x + 8.0,
                    "y": pill.y + 4.0,
                    "fill": INK,
                }
            )
    points = frame.trail
    for index in range(1, len(points)):
        alpha = 0.12 + 0.6 * (index / max(len(points) - 1, 1))
        ops.append(
            {
                "part": "trail",
                "op": "line",
                "x1": points[index - 1][0],
                "y1": points[index - 1][1],
                "x2": points[index][0],
                "y2": points[index][1],
                "stroke": (CYAN[0], CYAN[1], CYAN[2], alpha),
                "sw": 3.2,
            }
        )
    if frame.effect == "pulse":
        radius = 8.0 + 28.0 * frame.effect_p
        alpha = 0.7 * (1.0 - frame.effect_p)
        ops.append(
            {
                "part": "pulse",
                "op": "ellipse",
                "cx": frame.x,
                "cy": frame.y,
                "rx": radius,
                "ry": radius,
                "fill": (CYAN[0], CYAN[1], CYAN[2], alpha * 0.22),
                "stroke": (CYAN[0], CYAN[1], CYAN[2], alpha),
                "sw": 2.0,
            }
        )
    if frame.effect == "double":
        for lag in (0.0, 0.38):
            progressed = min(1.0, max(0.0, (frame.effect_p - lag) / 0.62))
            if progressed <= 0.0 and lag > 0.0:
                continue
            radius = 6.0 + 30.0 * progressed
            alpha = 0.8 * (1.0 - progressed)
            ops.append(
                {
                    "part": "double",
                    "op": "ellipse",
                    "cx": frame.x,
                    "cy": frame.y,
                    "rx": radius,
                    "ry": radius,
                    "fill": (CYAN[0], CYAN[1], CYAN[2], alpha * 0.16),
                    "stroke": (CYAN[0], CYAN[1], CYAN[2], alpha),
                    "sw": 2.4 if lag == 0.0 else 1.6,
                }
            )
    if frame.effect == "right":
        size = 16.0 + 44.0 * frame.effect_p
        alpha = 0.85 * (1.0 - frame.effect_p)
        ops.append(
            {
                "part": "right",
                "op": "roundrect",
                "x": frame.x - size / 2.0,
                "y": frame.y - size / 2.0,
                "w": size,
                "h": size,
                "r": 4.0,
                "fill": (RIGHT[0], RIGHT[1], RIGHT[2], alpha * 0.18),
                "stroke": (RIGHT[0], RIGHT[1], RIGHT[2], alpha),
                "sw": 2.4,
            }
        )
    if frame.effect == "typing":
        ops.append(
            {
                "part": "typing",
                "op": "roundrect",
                "x": frame.x + 12.0,
                "y": frame.y - 30.0,
                "w": 30.0,
                "h": 14.0,
                "r": 7.0,
                "fill": (0.05, 0.08, 0.12, 0.9),
            }
        )
        for index in range(3):
            phase = (frame.effect_p * 3.0 + index) % 3.0
            lit = 0.3 + 0.7 * max(0.0, 1.0 - abs(phase - 1.0))
            ops.append(
                {
                    "part": "typing",
                    "op": "ellipse",
                    "cx": frame.x + 19.0 + index * 8.0,
                    "cy": frame.y - 23.0,
                    "rx": 1.8,
                    "ry": 1.8,
                    "fill": (CYAN[0], CYAN[1], CYAN[2], lit),
                }
            )
    scale = frame.scale if frame.scale > 0 else 1.0
    for op in describe_cursor(frame.phase):
        ops.append(_shift(_scale_op(op, scale), frame.x, frame.y))
    return ops
