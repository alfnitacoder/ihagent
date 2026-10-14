"""Start and stop the iheye helper, and push agent state to it.

The helper is a separate process so AppKit's run loop does not share the
terminal UI thread. It is started in its own session and watches the parent
pid, so closing ihagent cannot leave the bubble behind.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .appearance import STATES
from .host import HostInfo
from .robot import normalize_skin, skin_from_env
from .panels import clean_chat_text, summarize_answer, trim_reply
from .protocol import connect_socket, decode, encode

INSTALL_HINT = "pip install 'hfagent[iheye]'"

_ON = {"1", "true", "yes", "on"}
_OFF = {"0", "false", "no", "off"}


def flag_enabled(name: str, default: bool = True, environ: dict[str, str] | None = None) -> bool:
    """Boolean env flag. Unset uses ``default``. Unknown text also uses ``default``."""
    env = os.environ if environ is None else environ
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    text = str(raw).strip().lower()
    if text in _ON:
        return True
    if text in _OFF:
        return False
    return default


def track_enabled(environ: dict[str, str] | None = None) -> bool:
    """``HFAGENT_IHEYE_TRACK`` — pupil follows the pointer and keyboard activity."""
    return flag_enabled("HFAGENT_IHEYE_TRACK", True, environ)


def summary_enabled(environ: dict[str, str] | None = None) -> bool:
    """``HFAGENT_IHEYE_SUMMARY`` — completion card under the eye."""
    return flag_enabled("HFAGENT_IHEYE_SUMMARY", True, environ)


def skin_name(environ: dict[str, str] | None = None) -> str:
    """``HFAGENT_IHEYE_SKIN`` — ``robot`` or ``classic``. Default is robot."""
    return skin_from_env(environ)


def enabled_from_env(environ: dict[str, str] | None = None, platform_name: str | None = None) -> bool:
    """``HFAGENT_IHEYE`` toggles the overlay. Unset means on for macOS only."""
    env = os.environ if environ is None else environ
    platform_name = sys.platform if platform_name is None else platform_name
    raw = env.get("HFAGENT_IHEYE")
    if raw is None or str(raw).strip() == "":
        return platform_name == "darwin"
    return str(raw).strip().lower() in _ON


def pyobjc_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("AppKit") is not None
    except (ImportError, ValueError):
        return False


@dataclass
class EyeStatus:
    running: bool
    reason: str
    message: str = ""


def helper_command(
    *,
    parent_pid: int,
    socket_path: str,
    position_path: str,
    host: HostInfo,
    executable: str | None = None,
    track: bool = True,
    summary: bool = True,
    skin: str = "robot",
) -> list[str]:
    return [
        executable or sys.executable,
        "-m",
        "hfagent.iheye",
        "--parent-pid",
        str(parent_pid),
        "--socket",
        socket_path,
        "--position",
        position_path,
        "--host-kind",
        host.kind,
        "--host-name",
        host.app_name,
        "--host-bundle",
        host.bundle_id,
        "--host-pid",
        str(host.pid or 0),
        "--ancestors",
        ",".join(str(pid) for pid in host.ancestor_pids),
        "--tty",
        host.tty,
        "--track",
        "1" if track else "0",
        "--summary",
        "1" if summary else "0",
        "--skin",
        normalize_skin(skin),
    ]


class EyeSupervisor:
    def __init__(
        self,
        *,
        socket_dir: Path | None = None,
        position_path: Path | None = None,
        log_path: Path | None = None,
        platform_name: str | None = None,
    ) -> None:
        self.socket_dir = Path(socket_dir) if socket_dir else Path(tempfile.gettempdir())
        self.position_path = position_path or (Path.home() / ".hfagent" / "iheye-position.json")
        self.log_path = log_path or (Path.home() / ".hfagent" / "iheye.log")
        self.platform_name = sys.platform if platform_name is None else platform_name
        self.state = "idle"
        self.track = track_enabled()
        self.summary_enabled = summary_enabled()
        self.skin = skin_from_env()
        self.host: HostInfo | None = None
        self.on_chat = None
        self.on_approval = None
        self._proc: subprocess.Popen | None = None
        self._sock = None
        self._log_fh = None
        self._pending: dict | None = None
        self._pending_msgs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._reader: threading.Thread | None = None
        self._socket_path = ""
        self._reply_at = 0.0
        self._hints: set[str] = set()
        self.status = EyeStatus(False, "stopped", "")

    @property
    def running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def start(self, host: HostInfo, *, parent_pid: int | None = None) -> EyeStatus:
        if self.running:
            self.host = host
            self.status = EyeStatus(True, "started", "iheye on")
            return self.status
        if self.platform_name != "darwin":
            self.status = EyeStatus(
                False,
                "not-macos",
                "iheye only runs on macOS",
            )
            return self.status
        if not pyobjc_available():
            self.status = EyeStatus(
                False,
                "missing-pyobjc",
                f"iheye needs PyObjC. {INSTALL_HINT}",
            )
            return self.status
        if self._proc is not None or self._thread is not None:
            self.stop()
        parent = os.getpid() if parent_pid is None else parent_pid
        self.socket_dir.mkdir(parents=True, exist_ok=True)
        socket_path = str(self.socket_dir / f"hfagent-iheye-{parent}.sock")
        self._socket_path = socket_path
        cmd = helper_command(
            parent_pid=parent,
            socket_path=socket_path,
            position_path=str(self.position_path),
            host=host,
            track=self.track,
            summary=self.summary_enabled,
            skin=self.skin,
        )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(self.log_path, "w", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=log_fh,
                start_new_session=True,
            )
        except OSError as exc:
            log_fh.close()
            self.status = EyeStatus(False, "spawn-failed", f"iheye failed to start: {exc}")
            return self.status
        self._proc = proc
        self._log_fh = log_fh
        self.host = host
        self._stopped.clear()
        self._pending = {"state": self.state}
        self._pending_msgs["options"] = {
            "track": self.track,
            "summary_enabled": self.summary_enabled,
        }
        self._thread = threading.Thread(target=self._flush_loop, name="iheye-flush", daemon=True)
        self._thread.start()
        self._reader = threading.Thread(target=self._reader_loop, name="iheye-read", daemon=True)
        self._reader.start()
        # The helper exits immediately when AppKit cannot start. Catch that
        # without holding up ihagent for long.
        if not self._survived(0.2):
            detail = self._log_tail()
            message = "iheye helper exited"
            if detail:
                message = f"{message}: {detail}"
            self.status = EyeStatus(False, "helper-exited", message)
            self.stop()
            return self.status
        self.status = EyeStatus(True, "started", "iheye on")
        return self.status

    def stop(self) -> None:
        self._stopped.set()
        with self._lock:
            self._send_locked({"cmd": "quit"})
            self._close_sock_locked()
            proc = self._proc
            self._proc = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                proc.kill()
        thread = self._thread
        self._thread = None
        reader = self._reader
        self._reader = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1)
        fh = self._log_fh
        self._log_fh = None
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass
        if self.status.reason == "started":
            self.status = EyeStatus(False, "stopped", "iheye off")

    def set_state(self, state: str) -> None:
        if state not in STATES:
            return
        with self._lock:
            self.state = state
            if self._proc is None:
                return
            self._pending = {"state": state}
            self._send_locked(self._pending)
            if self._sock is not None:
                self._pending = None

    def note_reply(self, text: str, final: bool = False) -> None:
        """Push the latest assistant text. Throttled unless this is the final cut."""
        if not self.running:
            return
        raw = text if isinstance(text, str) else str(text or "")
        if len(raw) > 8000:
            raw = raw[-8000:]
        message = {"reply": trim_reply(raw), "final": bool(final)}
        with self._lock:
            now = time.monotonic()
            if not final and now - self._reply_at < 0.08:
                self._pending_msgs["reply"] = message
                return
            self._reply_at = now
            self._enqueue_locked("reply", message)

    def note_summary(self, text: str) -> None:
        if not self.running or not self.summary_enabled:
            return
        body = summarize_answer(text)
        if not body:
            return
        with self._lock:
            self._enqueue_locked("summary", {"summary": body})

    def note_approval(self, tool: str, preview: str) -> None:
        if not self.running:
            return
        if not str(tool or "").strip():
            message: dict = {"approval": None}
        else:
            message = {"approval": {"tool": str(tool), "preview": str(preview or "")}}
        with self._lock:
            self._enqueue_locked("approval", message)

    def note_options(self, *, track: bool | None = None, summary: bool | None = None) -> None:
        message: dict = {}
        with self._lock:
            if track is not None:
                self.track = bool(track)
                message["track"] = self.track
            if summary is not None:
                self.summary_enabled = bool(summary)
                message["summary_enabled"] = self.summary_enabled
            if not message or self._proc is None:
                return
            self._enqueue_locked("options", message)

    def _enqueue_locked(self, key: str, message: dict) -> None:
        self._pending_msgs[key] = message
        self._send_locked(message)
        if self._sock is not None:
            self._pending_msgs.pop(key, None)

    def _survived(self, seconds: float) -> bool:
        proc = self._proc
        if proc is None:
            return False
        try:
            proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            return True
        return proc.poll() is None

    def _log_tail(self) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return " ".join(lines[-3:])[:300]

    def _flush_loop(self) -> None:
        while not self._stopped.wait(0.05):
            with self._lock:
                proc = self._proc
                if proc is None or proc.poll() is not None:
                    continue
                if self._pending is None and not self._pending_msgs:
                    self._ensure_connected_locked()
                    continue
                if not self._ensure_connected_locked():
                    continue
                if self._pending is not None:
                    self._send_locked(self._pending)
                    if self._sock is not None:
                        self._pending = None
                for key in list(self._pending_msgs):
                    self._send_locked(self._pending_msgs[key])
                    if self._sock is None:
                        break
                    self._pending_msgs.pop(key, None)

    def _reader_loop(self) -> None:
        buf = b""
        while not self._stopped.is_set():
            with self._lock:
                sock = self._sock
            if sock is None:
                if self._stopped.wait(0.05):
                    return
                continue
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                continue
            except OSError:
                with self._lock:
                    if self._sock is sock:
                        self._close_sock_locked()
                buf = b""
                continue
            if not chunk:
                with self._lock:
                    if self._sock is sock:
                        self._close_sock_locked()
                buf = b""
                continue
            buf += chunk
            inbound: list[dict] = []
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                msg = decode(raw)
                if msg is not None:
                    inbound.append(msg)
            if len(buf) > 1_000_000:
                buf = b""
            for msg in inbound:
                self._dispatch(msg)

    def _dispatch(self, msg: dict) -> None:
        cmd = msg.get("cmd")
        try:
            if cmd == "chat":
                text = clean_chat_text(msg.get("text") or "")
                handler = self.on_chat
                if text and handler is not None:
                    handler(text)
                return
            if cmd == "approve":
                handler = self.on_approval
                if handler is not None:
                    handler(bool(msg.get("yes")))
                return
            if cmd == "hint":
                text = str(msg.get("text") or "").strip()
                if text and text not in self._hints:
                    self._hints.add(text)
                    print(text, file=sys.stderr)
        except Exception:
            return

    def _ensure_connected_locked(self) -> bool:
        if self._sock is not None:
            return True
        if not self._socket_path:
            return False
        try:
            self._sock = connect_socket(self._socket_path, timeout=0.05)
            self._sock.settimeout(0.3)
        except OSError:
            self._sock = None
            return False
        return True

    def _send_locked(self, message: dict) -> None:
        if self._sock is None and not self._ensure_connected_locked():
            return
        if self._sock is None:
            return
        try:
            self._sock.sendall(encode(message))
        except OSError:
            self._close_sock_locked()

    def _close_sock_locked(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


_SINGLETON: EyeSupervisor | None = None
_SINGLETON_LOCK = threading.Lock()


def supervisor() -> EyeSupervisor:
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = EyeSupervisor()
        return _SINGLETON


def reset_supervisor(control: EyeSupervisor | None = None) -> EyeSupervisor:
    """Replace the process-wide supervisor. Tests use this to avoid leaks."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is not None and _SINGLETON is not control:
            _SINGLETON.stop()
        _SINGLETON = control or EyeSupervisor()
        return _SINGLETON
