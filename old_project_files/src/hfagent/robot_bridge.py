"""Localhost SSE bridge from IHAgent to the web robot.

Off unless ``HFAGENT_ROBOT_BRIDGE`` is ``1``, ``true``, ``yes``, or ``on``.
Listens on ``127.0.0.1`` and ``HFAGENT_ROBOT_PORT`` (default 8765).

Maps real agent phases only. This process has no microphone and no speech
audio, so the bridge never emits ``listening``, ``speaking``, a mic level,
or a progress percent.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue
from typing import Callable

ROBOT_STATES = (
    "idle",
    "thinking",
    "listening",
    "speaking",
    "working",
    "success",
    "error",
)

_IHEYE_MAP = {
    "idle": "idle",
    "thinking": "thinking",
    "waiting": "idle",
    "done": "success",
}

_SUCCESS_IDLE_S = 3.2


def bridge_enabled(environ: dict | None = None) -> bool:
    env = os.environ if environ is None else environ
    raw = str(env.get("HFAGENT_ROBOT_BRIDGE", "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _port_from_env(environ: dict | None = None) -> int:
    env = os.environ if environ is None else environ
    raw = str(env.get("HFAGENT_ROBOT_PORT", "8765")).strip()
    try:
        port = int(raw)
    except ValueError:
        port = 8765
    if port < 0 or port > 65535:
        return 8765
    return port


class Bridge:
    """In-memory event state plus an optional SSE server."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: list[Queue] = []
        self._state = "idle"
        self._message = ""
        self._assistant = ""
        self._assist_fresh = True
        self._failed = False
        self._gen = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.chat_handler: Callable | None = None
        self.port = 0

    @property
    def state(self) -> str:
        return self._state

    @property
    def message(self) -> str:
        return self._message

    @property
    def mic(self) -> bool:
        return False

    @property
    def audio(self) -> bool:
        return False

    def note_iheye(self, state: str) -> None:
        """Map an iheye phase. ``waiting`` becomes idle. ``done`` is success unless the turn failed."""
        mapped = _IHEYE_MAP.get(str(state or "").strip().lower())
        if mapped is None:
            return
        with self._lock:
            self._gen += 1
            if state == "thinking":
                self._failed = False
                self._assistant = ""
                self._assist_fresh = True
                self._message = ""
                self._state = "thinking"
            elif state == "done":
                self._state = "error" if self._failed else "success"
            else:
                self._state = mapped
            self._broadcast_locked("state")
            if self._state == "success":
                self._schedule_success_idle_locked()

    def note_working(self, name: str) -> None:
        label = str(name or "tool").strip() or "tool"
        with self._lock:
            self._gen += 1
            self._state = "working"
            self._message = f"Running {label}"[:500]
            self._assistant = ""
            self._assist_fresh = True
            self._broadcast_locked("state")

    def note_error(self, message: str) -> None:
        with self._lock:
            self._gen += 1
            self._failed = True
            self._state = "error"
            text = str(message or "").strip()
            if text:
                self._message = text[:500]
            self._broadcast_locked("state")

    def note_assistant(self, text: str, final: bool = False) -> None:
        """Show assistant text. Does not switch to speaking — there is no audio."""
        with self._lock:
            if final:
                body = str(text or "").strip()
                if body:
                    self._assistant = str(text)
                    self._message = body[:500]
                self._assist_fresh = True
                self._broadcast_locked("message", final=True)
                return
            chunk = str(text or "")
            if not chunk:
                return
            if self._assist_fresh:
                self._assistant = ""
                self._assist_fresh = False
            self._assistant += chunk
            self._message = self._assistant[-500:]
            self._broadcast_locked("message", final=False)

    def note_approval(self, tool: str, preview: str = "") -> None:
        label = str(tool or "").strip()
        if not label:
            return
        with self._lock:
            self._message = f"Approval needed: {label}"[:500]
            self._broadcast_locked("message", final=True)

    def submit_chat(self, text: str, handler: Callable | None = None) -> tuple[bool, str]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False, "empty message"
        if handler is None:
            handler = self.chat_handler
        if handler is None:
            try:
                from .iheye.supervisor import supervisor as get_supervisor

                handler = get_supervisor().on_chat
            except Exception:
                handler = None
        if not callable(handler):
            return False, "IHAgent is not accepting chat right now."
        try:
            handler(cleaned)
        except TypeError:
            try:
                handler(cleaned, None)
            except Exception as exc:
                return False, str(exc) or "chat handler failed"
        except Exception as exc:
            return False, str(exc) or "chat handler failed"
        return True, ""

    def start(self, port: int | None = None) -> bool:
        if self._server is not None:
            return True
        chosen = _port_from_env() if port is None else int(port)
        try:
            server = ThreadingHTTPServer(("127.0.0.1", chosen), _Handler)
            server.daemon_threads = True
        except OSError as exc:
            print(
                f"robot bridge: could not listen on 127.0.0.1:{chosen} ({exc})",
                file=sys.stderr,
            )
            return False
        server.bridge = self  # type: ignore[attr-defined]
        self._server = server
        self.port = int(server.server_address[1])
        thread = threading.Thread(
            target=server.serve_forever,
            name="ihagent-robot-bridge",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        print(f"robot bridge: http://127.0.0.1:{self.port}/events", file=sys.stderr)
        return True

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is None:
            return
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            try:
                client.put_nowait(None)
            except Exception:
                pass
        server.shutdown()
        server.server_close()

    def _schedule_success_idle_locked(self) -> None:
        gen = self._gen

        def later() -> None:
            with self._lock:
                if self._gen != gen or self._state != "success":
                    return
                self._gen += 1
                self._state = "idle"
                self._broadcast_locked("state")

        timer = threading.Timer(_SUCCESS_IDLE_S, later)
        timer.daemon = True
        timer.start()

    def _payload(self, kind: str, final: bool = False) -> dict:
        if kind == "message":
            return {
                "type": "message",
                "message": self._message,
                "final": final,
                "mic": False,
                "audio": False,
            }
        return {
            "type": "state",
            "state": self._state,
            "message": self._message,
            "mic": False,
            "audio": False,
        }

    def _broadcast_locked(self, kind: str, final: bool = False) -> None:
        if not self._clients:
            return
        frame = _frame(self._payload(kind, final=final))
        for client in list(self._clients):
            try:
                client.put_nowait(frame)
            except Exception:
                pass

    def add_client(self, client: Queue) -> bytes:
        with self._lock:
            self._clients.append(client)
            return _frame(self._payload("state"))

    def remove_client(self, client: Queue) -> None:
        with self._lock:
            try:
                self._clients.remove(client)
            except ValueError:
                pass


def _frame(payload: dict) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {body}\n\n".encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _bridge(self) -> Bridge:
        return self.server.bridge  # type: ignore[attr-defined]

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/health", "/"}:
            bridge = self._bridge()
            body = json.dumps(
                {"ok": True, "state": bridge.state, "mic": False, "audio": False}
            ).encode("utf-8")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path != "/events":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        bridge = self._bridge()
        client: Queue = Queue()
        hello = bridge.add_client(client)
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(hello)
            self.wfile.flush()
            while True:
                try:
                    item = client.get(timeout=15)
                except Exception:
                    item = b": ping\n\n"
                if item is None:
                    break
                self.wfile.write(item)
                self.wfile.flush()
        except Exception:
            pass
        finally:
            bridge.remove_client(client)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/message":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send_json(400, {"ok": False, "error": "invalid JSON"})
            return
        if not isinstance(data, dict):
            self._send_json(400, {"ok": False, "error": "invalid JSON"})
            return
        ok, err = self._bridge().submit_chat(str(data.get("text") or ""))
        self._send_json(200 if ok else 409, {"ok": ok, "error": err})

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


bridge = Bridge()


def attach_ui(ui: object) -> None:
    """Mirror UI callbacks into the bridge. Safe to call more than once."""
    if ui is None or getattr(ui, "_ih_robot_wrapped", False):
        return
    orig_error = getattr(ui, "on_error", None)
    orig_action = getattr(ui, "on_action", None)
    orig_delta = getattr(ui, "on_assistant_delta", None)
    orig_done = getattr(ui, "on_assistant_done", None)

    def on_error(message: str) -> None:
        try:
            bridge.note_error(str(message or ""))
        except Exception:
            pass
        if callable(orig_error):
            orig_error(message)

    def on_action(tool_name: str, arguments: dict) -> None:
        try:
            bridge.note_working(str(tool_name or ""))
        except Exception:
            pass
        if callable(orig_action):
            orig_action(tool_name, arguments)

    def on_assistant_delta(text: str) -> None:
        try:
            bridge.note_assistant(str(text or ""), final=False)
        except Exception:
            pass
        if callable(orig_delta):
            orig_delta(text)

    def on_assistant_done(text: str) -> None:
        try:
            bridge.note_assistant(str(text or ""), final=True)
        except Exception:
            pass
        if callable(orig_done):
            orig_done(text)

    try:
        ui.on_error = on_error  # type: ignore[attr-defined]
        ui.on_action = on_action  # type: ignore[attr-defined]
        ui.on_assistant_delta = on_assistant_delta  # type: ignore[attr-defined]
        ui.on_assistant_done = on_assistant_done  # type: ignore[attr-defined]
        ui._ih_robot_wrapped = True  # type: ignore[attr-defined]
    except Exception:
        return


def start_if_enabled() -> bool:
    if not bridge_enabled():
        return False
    return bridge.start()


def stop() -> None:
    try:
        bridge.stop()
    except Exception:
        pass
