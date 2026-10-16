"""Talk to the ihmouse overlay, or do nothing when it cannot be shown.

The helper is a separate process so AppKit and Qt own their main thread
and a blocked approval prompt does not freeze the glide. Tests inject a
recording client. Under pytest the default client never starts a window.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import Any, Callable

from .overlay_motion import Cue
from .overlay_window import backend_note, module_available


def overlay_requested() -> bool:
    """On unless ``HFAGENT_MOUSE_OVERLAY`` is explicitly off. Default is on."""
    raw = os.environ.get("HFAGENT_MOUSE_OVERLAY", "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def encode_message(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":")) + "\n"


def decode_message(line: str) -> dict | None:
    text = (line or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def prepare_launch(note: str, launcher: Callable[[], Any]) -> tuple[Any, str]:
    """Skip the process when ``note`` already explains why. Launcher errors degrade."""
    if note:
        return None, note
    try:
        return launcher(), ""
    except OSError as exc:
        return None, (
            f"ihmouse overlay did not start ({exc}). The system pointer still moves."
        )


class NullOverlay:
    """No window. Every call succeeds so the real pointer still runs."""

    failed = ""

    def propose(self, cue: Cue, origin: tuple[float, float] | None = None) -> None:
        return None

    def act(self, effect: str) -> None:
        return None

    def release(self) -> None:
        return None

    def hide(self) -> None:
        return None

    def suspend(self) -> None:
        return None

    def resume(self) -> None:
        return None

    def wait_arrived(self, timeout: float = 0.75) -> bool:
        return True

    def wait_effect(self, timeout: float = 0.16) -> None:
        return None

    def close(self) -> None:
        return None


class RecordingOverlay(NullOverlay):
    """In-memory client for tests. Calls are instant."""

    def __init__(self) -> None:
        self.events: list[tuple] = []

    def propose(self, cue: Cue, origin: tuple[float, float] | None = None) -> None:
        self.events.append(("propose", cue, origin))

    def act(self, effect: str) -> None:
        self.events.append(("act", effect))

    def release(self) -> None:
        self.events.append(("release",))

    def hide(self) -> None:
        self.events.append(("hide",))

    def suspend(self) -> None:
        self.events.append(("suspend",))

    def resume(self) -> None:
        self.events.append(("resume",))

    def wait_arrived(self, timeout: float = 0.75) -> bool:
        self.events.append(("wait_arrived",))
        return True

    def wait_effect(self, timeout: float = 0.16) -> None:
        self.events.append(("wait_effect",))

    def close(self) -> None:
        self.events.append(("close",))


class LiveOverlay:
    """Spawn ``python -m hfagent.desktop.overlay_main`` on the first propose."""

    def __init__(self) -> None:
        self.failed = ""
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._arrived = threading.Event()
        self._arrived.set()
        self._suspend_ack = threading.Event()
        self._suspend_ack.set()
        self._suspend_seq = 0
        self._seq = 0
        self._waiting = 0
        self._noted = False
        self._reader: threading.Thread | None = None

    def propose(self, cue: Cue, origin: tuple[float, float] | None = None) -> None:
        if not self._ensure():
            self._arrived.set()
            return
        self._seq += 1
        seq = self._seq
        self._waiting = seq
        self._arrived.clear()
        payload: dict[str, Any] = {
            "op": "propose",
            "seq": seq,
            "x": cue.x,
            "y": cue.y,
            "label": cue.label,
            "action": cue.action,
        }
        if cue.bounds is not None:
            box = cue.bounds
            payload["bounds"] = [box.x, box.y, box.width, box.height]
        if origin is not None:
            payload["ox"] = origin[0]
            payload["oy"] = origin[1]
        self._send(payload)

    def act(self, effect: str) -> None:
        if self._proc is None:
            return
        self._send({"op": "act", "effect": effect})

    def release(self) -> None:
        if self._proc is None:
            return
        self._send({"op": "release"})

    def hide(self) -> None:
        if self._proc is None:
            return
        self._send({"op": "hide"})

    def suspend(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            return
        self._seq += 1
        seq = self._seq
        self._suspend_seq = seq
        self._suspend_ack.clear()
        self._send({"op": "suspend", "seq": seq})
        self._suspend_ack.wait(0.5)

    def resume(self) -> None:
        if self._proc is None:
            return
        self._send({"op": "resume"})

    def wait_arrived(self, timeout: float = 1.0) -> bool:
        if self._proc is None:
            return True
        return self._arrived.wait(timeout)

    def wait_effect(self, timeout: float = 0.16) -> None:
        if self._proc is None or self._proc.poll() is not None:
            return
        import time

        time.sleep(min(max(timeout, 0.0), 0.2))

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                proc.stdin.write(encode_message({"op": "quit"}))
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=0.4)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _ensure(self) -> bool:
        if self.failed:
            return False
        if self._proc is not None and self._proc.poll() is None:
            return True
        note = backend_note(sys.platform, os.environ, module_available)
        proc, note = prepare_launch(note, self._popen)
        if note:
            self.failed = note
            self._note_failure()
            return False
        self._proc = proc
        self._ready.clear()
        self._reader = threading.Thread(target=self._read, name="ihmouse-overlay", daemon=True)
        self._reader.start()
        if not self._ready.wait(2.0):
            self.failed = self.failed or "ihmouse overlay did not start. The system pointer still moves."
            self._note_failure()
            self.close()
            return False
        if self.failed:
            self._note_failure()
            self.close()
            return False
        return self._proc is not None and self._proc.poll() is None

    def _popen(self) -> subprocess.Popen:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        return subprocess.Popen(
            [sys.executable, "-m", "hfagent.desktop.overlay_main", "--parent", str(os.getpid())],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
        )

    def _send(self, payload: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            self._arrived.set()
            return
        try:
            with self._lock:
                proc.stdin.write(encode_message(payload))
                proc.stdin.flush()
        except Exception as exc:
            self.failed = f"ihmouse overlay closed ({exc}). The system pointer still moves."
            self._arrived.set()
            self._suspend_ack.set()
            self._note_failure()

    def _read(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            self._ready.set()
            self._arrived.set()
            return
        for line in proc.stdout:
            message = decode_message(line)
            if not message:
                continue
            event = message.get("event")
            if event == "ready":
                self._ready.set()
            elif event == "failed":
                self.failed = str(message.get("error") or "ihmouse overlay failed")
                self._ready.set()
                self._arrived.set()
            elif event == "arrived" and message.get("seq") == self._waiting:
                self._arrived.set()
            elif event == "suspended" and message.get("seq") == self._suspend_seq:
                self._suspend_ack.set()
        if not self._ready.is_set():
            self.failed = self.failed or "ihmouse overlay exited. The system pointer still moves."
            self._ready.set()
        self._arrived.set()
        self._suspend_ack.set()

    def _note_failure(self) -> None:
        if self._noted or not self.failed:
            return
        self._noted = True
        try:
            from .session import get_session, stamp

            get_session().remember(stamp(self.failed))
        except Exception:
            pass


_client: NullOverlay | RecordingOverlay | LiveOverlay | None = None


def _testing() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def get_overlay() -> NullOverlay | RecordingOverlay | LiveOverlay:
    global _client
    if _client is None:
        _client = NullOverlay() if _testing() else LiveOverlay()
    return _client


def peek_overlay():
    return _client


def set_overlay(client) -> None:
    global _client
    _client = client


def rearm_overlay() -> None:
    """Allow another start after ``/mouse on`` if the helper had exited."""
    client = _client
    if isinstance(client, LiveOverlay):
        if client._proc is None or client._proc.poll() is not None:
            client.failed = ""
            client._noted = False
            client._proc = None


def hide_overlay() -> None:
    client = _client
    if client is None:
        return
    try:
        client.hide()
    except Exception:
        pass


def reset_overlay() -> None:
    global _client
    client = _client
    _client = None
    if client is not None:
        try:
            client.close()
        except Exception:
            pass


def overlay_problem() -> str:
    client = _client
    if client is None:
        return ""
    return getattr(client, "failed", "") or ""
