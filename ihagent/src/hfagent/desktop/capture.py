"""Grab the focused window without a background timer.

macOS reuses iheye's screenshot check (``screencapture`` plus the Screen
Recording hint) and does not downscale with ``sips``, because that would
drop the pixel-to-point ratio. Other platforms use pyautogui when the
desktop extra is installed.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile

from .geometry import Rect
from .permissions import MISSING_DESKTOP, screen_hint


class CaptureError(Exception):
    """``str(exc)`` is safe to show to the model."""


def png_size(data: bytes) -> tuple[int, int]:
    """Width and height from a PNG IHDR. No image library required."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a png")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        raise ValueError("png has no size")
    return width, height


def fit_png(
    png: bytes,
    pixel_width: int,
    pixel_height: int,
    max_edge: int = 1600,
) -> tuple[bytes, int, int]:
    """Shrink a huge capture so the vision request stays small.

    The returned pixel size is what the model sees. Pointer mapping uses
    that size against the original window rectangle, so the click still
    lands on the same point.
    """
    try:
        width, height = png_size(png)
    except ValueError:
        return png, pixel_width, pixel_height
    if max(width, height) <= max_edge:
        return png, width, height
    try:
        from PIL import Image
    except ImportError:
        return png, width, height
    image = Image.open(io.BytesIO(png))
    image.thumbnail((max_edge, max_edge))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    return data, image.size[0], image.size[1]


def grab_rect(rect: Rect) -> tuple[bytes, int, int]:
    """Return PNG bytes and the pixel size of that image."""
    if rect.width <= 1 or rect.height <= 1:
        raise CaptureError("Error: the focused window has no size to capture.")
    if sys.platform == "darwin":
        return _grab_macos(rect)
    return _grab_pyautogui(rect)


def _grab_macos(rect: Rect) -> tuple[bytes, int, int]:
    from ..iheye.look import ShotResult, interpret_screenshot

    handle, path = tempfile.mkstemp(prefix="hfagent-mouse-", suffix=".png")
    os.close(handle)
    region = f"{int(rect.x)},{int(rect.y)},{int(rect.width)},{int(rect.height)}"
    try:
        try:
            proc = subprocess.run(
                ["screencapture", "-R", region, "-x", path],
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CaptureError(f"Error: screencapture failed ({exc}).") from exc
        try:
            size = os.path.getsize(path) if os.path.isfile(path) else 0
        except OSError:
            size = 0
        problem = interpret_screenshot(
            ShotResult(ok=proc.returncode == 0, stderr=proc.stderr or "", size=size)
        )
        if problem:
            text = problem if problem.startswith("Error:") else f"Error: {problem}"
            raise CaptureError(text)
        try:
            data = open(path, "rb").read()
        except OSError as exc:
            raise CaptureError(f"Error: could not read the screenshot ({exc}).") from exc
        try:
            width, height = png_size(data)
        except ValueError as exc:
            raise CaptureError("Error: screencapture did not write a PNG.") from exc
        return data, width, height
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _grab_pyautogui(rect: Rect) -> tuple[bytes, int, int]:
    try:
        import pyautogui
    except ImportError as exc:
        raise CaptureError(MISSING_DESKTOP) from exc
    try:
        image = pyautogui.screenshot(
            region=(int(rect.x), int(rect.y), int(max(rect.width, 1)), int(max(rect.height, 1)))
        )
    except Exception as exc:
        message = str(exc).lower()
        if any(word in message for word in ("permission", "screen", "display", "authorization")):
            raise CaptureError(screen_hint(sys.platform)) from exc
        raise CaptureError(f"Error: could not capture the screen ({exc}).") from exc
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    width, height = image.size
    if width <= 1 or height <= 1:
        raise CaptureError(screen_hint(sys.platform))
    return data, int(width), int(height)
