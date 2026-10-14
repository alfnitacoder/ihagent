"""Remember where the user dragged the iheye bubble."""

from __future__ import annotations

import json
from pathlib import Path


def load_position(path: Path | str) -> tuple[float, float] | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return float(data["x"]), float(data["y"])
    except (KeyError, TypeError, ValueError):
        return None


def save_position(path: Path | str, x: float, y: float) -> None:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"x": x, "y": y}) + "\n"
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(dest)


def clamp_position(
    x: float,
    y: float,
    origin_x: float,
    origin_y: float,
    width: float,
    height: float,
    panel_w: float,
    panel_h: float,
) -> tuple[float, float]:
    """Keep the bubble inside a screen rectangle. Origin is the bottom-left."""
    max_x = origin_x + max(0.0, width - panel_w)
    max_y = origin_y + max(0.0, height - panel_h)
    return min(max(x, origin_x), max_x), min(max(y, origin_y), max_y)


def drop_origin(
    eye_x: float,
    eye_y: float,
    eye_w: float,
    eye_h: float,
    drop_w: float,
    drop_h: float,
    origin_x: float,
    origin_y: float,
    width: float,
    height: float,
    gap: float = 6.0,
) -> tuple[float, float]:
    """Place the card under the eye. If that would leave the screen, put it above."""
    x = eye_x + (eye_w - drop_w) / 2.0
    below = eye_y - drop_h - gap
    if below >= origin_y:
        y = below
    else:
        y = eye_y + eye_h + gap
    return clamp_position(x, y, origin_x, origin_y, width, height, drop_w, drop_h)


def default_position(
    origin_x: float,
    origin_y: float,
    width: float,
    height: float,
    panel_w: float,
    panel_h: float,
    margin: float = 18.0,
) -> tuple[float, float]:
    """Top-right of the visible screen, in AppKit coordinates (origin bottom-left)."""
    x = origin_x + width - panel_w - margin
    y = origin_y + height - panel_h - margin
    return clamp_position(x, y, origin_x, origin_y, width, height, panel_w, panel_h)
