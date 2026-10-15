"""Rasterize the bot skin. The overlay cannot launch on Linux.

Writes a sheet of every pose and, with ffmpeg on PATH, a short mp4.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from hfagent.iheye.bot import BOT_H, BOT_W, bot_pose, describe_bot, rasterize

POSES = (
    ("idle", "idle", dict(state="idle", phase=2.2)),
    ("thinking", "thinking", dict(state="thinking", phase=1.4)),
    ("listening", "listening — unused", dict(state="idle", phase=1.0, mood="listening")),
    ("speaking", "speaking", dict(state="thinking", phase=1.05, speaking=True)),
    ("working", "working", dict(state="thinking", phase=1.3, working=True)),
    ("success", "success", dict(state="done", phase=1.15)),
    ("error", "error / approval", dict(state="waiting", phase=0.8)),
)

# (label, seconds, pose kwargs, phase offset). Success runs long enough to wave, then the done state sits idle.
SEGMENTS = (
    ("idle", 0.9, dict(state="idle"), 0.4),
    ("thinking", 0.9, dict(state="thinking"), 0.2),
    ("speaking", 0.9, dict(state="thinking", speaking=True), 0.2),
    ("working", 0.9, dict(state="thinking", working=True), 0.2),
    ("success", 1.2, dict(state="done"), 0.0),
    ("back to idle", 0.7, dict(state="done"), 8.6),
    ("error", 0.9, dict(state="waiting"), 0.2),
)


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _backdrop(width: int, height: int) -> Image.Image:
    image = Image.new("RGBA", (width, height), (8, 14, 22, 255))
    draw = ImageDraw.Draw(image)
    step = 28
    color = (18, 32, 44, 255)
    for x in range(0, width, step):
        draw.line([(x, 0), (x, height)], fill=color)
    for y in range(0, height, step):
        draw.line([(0, y), (width, y)], fill=color)
    return image


def _cell(pose, scale: int = 3) -> Image.Image:
    ops = describe_bot(pose, BOT_W, BOT_H)
    sharp = rasterize(ops, BOT_W, BOT_H, scale=scale)
    return sharp.resize((int(BOT_W * 2), int(BOT_H * 2)), Image.Resampling.LANCZOS)


def write_sheet(path: Path) -> None:
    scale = 2
    cell_w, cell_h = int(BOT_W * scale), int(BOT_H * scale)
    label_h = 36
    pad = 18
    width = pad + len(POSES) * (cell_w + pad)
    height = pad + cell_h + label_h + pad
    sheet = _backdrop(width, height)
    font = _font(18)
    for index, (mood, label, kwargs) in enumerate(POSES):
        pose = bot_pose(**kwargs)
        assert pose.mood == mood
        cell = _cell(pose)
        x = pad + index * (cell_w + pad)
        y = pad
        sheet.alpha_composite(cell, (x, y))
        draw = ImageDraw.Draw(sheet)
        draw.text((x + 8, y + cell_h + 6), label, fill=(214, 226, 236, 255), font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(path, quality=92)


def write_video(path: Path, fps: int = 12) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return
    font = _font(22)
    with tempfile.TemporaryDirectory(prefix="iheye-bot-") as tmp:
        folder = Path(tmp)
        frame_i = 0
        for label, seconds, kwargs, phase0 in SEGMENTS:
            count = max(1, int(round(seconds * fps)))
            for step in range(count):
                phase = phase0 + (step / fps) * 3.6
                pose = bot_pose(phase=phase, **kwargs)
                cell = _cell(pose, scale=2)
                frame = _backdrop(cell.width, cell.height + 40)
                frame.alpha_composite(cell, (0, 0))
                ImageDraw.Draw(frame).text((16, cell.height + 8), label, fill=(214, 226, 236, 255), font=font)
                frame.convert("RGB").save(folder / f"f{frame_i:04d}.png")
                frame_i += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-framerate",
                str(fps),
                "-i",
                str(folder / "f%04d.png"),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, required=True)
    parser.add_argument("--video", type=Path, default=None)
    args = parser.parse_args()
    write_sheet(args.sheet)
    if args.video is not None:
        write_video(args.video)


if __name__ == "__main__":
    main()
