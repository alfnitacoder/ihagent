"""Start and stop the iheye helper, and push agent state to it.

The helper is a separate process so AppKit's run loop does not share the
terminal UI thread. It is started in its own session and watches the parent
pid, so closing ihagent cannot leave the bubble behind.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .appearance import STATES
from .host import HostInfo
from .protocol import connect_socket, encode

INSTALL_HINT = "pip install 'hfagent[iheye]'"

_ON = {"1", "true", "yes", "on"}


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
        self.host: HostInfo | None = None
        self._proc: subprocess.Popen | None = None
        self._sock = None
        self._log_fh = None
        self._pending: dict | None = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket_path = ""
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
        self._thread = threading.Thread(target=self._flush_loop, name="iheye-flush", daemon=True)
        self._thread.start()
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
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
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
                if self._pending is None or self._proc is None or self._proc.poll() is not None:
                    continue
                if not self._ensure_connected_locked():
                    continue
                self._send_locked(self._pending)
                if self._sock is not None:
                    self._pending = None

    def _ensure_connected_locked(self) -> bool:
        if self._sock is not None:
            return True
        if not self._socket_path:
            return False
        try:
            self._sock = connect_socket(self._socket_path, timeout=0.05)
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
