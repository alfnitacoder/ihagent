"""Draw list for the ihmouse robot. No GUI imports.

The robot matches the iheye bot: white body, black face, cyan eyes, and a
glowing cyan ring. Coordinates are pointer space, y down. The hotspot is
the cyan dot. The body sits just above it so the target stays visible.
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


def describe_cursor(phase: float) -> list[dict]:
    """Robot in a local box whose origin is the click point."""
    glow = 0.16 + 0.08 * (0.5 + 0.5 * math.sin(phase * 3.0))
    return [
        {
            "part": "ring",
            "op": "ellipse",
            "cx": 0.0,
            "cy": -6.0,
            "rx": 16.0,
            "ry": 6.0,
            "fill": (CYAN[0], CYAN[1], CYAN[2], glow),
            "stroke": CYAN,
            "sw": 1.6,
        },
        {
            "part": "body",
            "op": "ellipse",
            "cx": 0.0,
            "cy": -20.0,
            "rx": 11.0,
            "ry": 12.0,
            "fill": WHITE,
            "stroke": METAL,
            "sw": 1.0,
        },
        {
            "part": "body",
            "op": "ellipse",
            "cx": 0.0,
            "cy": -33.0,
            "rx": 10.0,
            "ry": 9.0,
            "fill": WHITE,
            "stroke": METAL,
            "sw": 1.0,
        },
        {
            "part": "face",
            "op": "roundrect",
            "x": -7.0,
            "y": -37.5,
            "w": 14.0,
            "h": 9.0,
            "r": 3.0,
            "fill": BLACK,
        },
        {
            "part": "eye",
            "op": "ellipse",
            "cx": -3.2,
            "cy": -33.2,
            "rx": 1.7,
            "ry": 1.7,
            "fill": CYAN,
        },
        {
            "part": "eye",
            "op": "ellipse",
            "cx": 3.2,
            "cy": -33.2,
            "rx": 1.7,
            "ry": 1.7,
            "fill": CYAN,
        },
        {
            "part": "body",
            "op": "line",
            "x1": 0.0,
            "y1": -42.0,
            "x2": 0.0,
            "y2": -46.0,
            "stroke": CYAN,
            "sw": 1.4,
        },
        {
            "part": "eye",
            "op": "ellipse",
            "cx": 0.0,
            "cy": -47.0,
            "rx": 1.8,
            "ry": 1.8,
            "fill": CYAN,
        },
        {
            "part": "hotspot",
            "op": "ellipse",
            "cx": 0.0,
            "cy": 0.0,
            "rx": 2.4,
            "ry": 2.4,
            "fill": CYAN,
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
    return item


def describe_scene(frame: Frame, virtual: Rect | None = None) -> list[dict]:
    """Highlight, label, pulse or typing dots, then the robot. Pointer space."""
    if not frame.visible or frame.opacity <= 0.0:
        return []
    ops: list[dict] = []
    if frame.bounds is not None:
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
        if frame.label:
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
    for op in describe_cursor(frame.phase):
        ops.append(_shift(op, frame.x, frame.y))
    return ops
