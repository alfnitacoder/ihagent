#!/usr/bin/env python3
"""Rasterize the Qt iheye skins with QT_QPA_PLATFORM=offscreen.

Writes one PNG per skin. The bot frame is the idle pose; a second file shows
the working pose (chest light, faster ring). No window is shown.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hfagent.iheye.qt_paint import save_skin_png  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    dest = Path(argv[1]) if argv and len(argv) > 1 else ROOT.parent / "docs" / "images"
    dest.mkdir(parents=True, exist_ok=True)
    frames = [
        ("iheye-qt-bot.png", "bot", {"state": "idle", "phase": 1.2}),
        ("iheye-qt-bot-working.png", "bot", {"state": "thinking", "phase": 2.4, "working": True}),
        ("iheye-qt-classic.png", "classic", {"state": "thinking", "phase": 1.4}),
        ("iheye-qt-grok.png", "grok", {"state": "idle", "phase": 1.2}),
        ("iheye-qt-robot.png", "robot", {"state": "thinking", "phase": 2.0}),
    ]
    for name, skin, pose in frames:
        path = dest / name
        save_skin_png(str(path), skin, scale=2, **pose)
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
