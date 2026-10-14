"""Newline-delimited JSON between ihagent and the iheye helper."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import Callable
from typing import Any


def encode(message: dict[str, Any]) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: bytes | str) -> dict[str, Any] | None:
    if isinstance(line, bytes):
        text = line.decode("utf-8", "replace").strip()
    else:
        text = line.strip()
    if not text:
        return None
    try:
        msg = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(msg, dict):
        return None
    return msg


class EyeServer:
    """Accepts one client at a time and hands each JSON object to ``on_message``."""

    def __init__(self, path: str, on_message: Callable[[dict[str, Any]], None]) -> None:
        self.path = path
        self._on_message = on_message
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        sock.listen(1)
        sock.settimeout(0.3)
        self._sock = sock
        self._thread = threading.Thread(target=self._loop, name="iheye-ipc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        conn: socket.socket | None = None
        buf = b""
        while not self._stop.is_set():
            if conn is None:
                try:
                    conn, _addr = sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                conn.settimeout(0.3)
                buf = b""
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                self._close(conn)
                conn = None
                continue
            if not chunk:
                self._close(conn)
                conn = None
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                msg = decode(raw)
                if msg is not None:
                    try:
                        self._on_message(msg)
                    except Exception:
                        pass
        if conn is not None:
            self._close(conn)

    @staticmethod
    def _close(conn: socket.socket) -> None:
        try:
            conn.close()
        except OSError:
            pass


def connect_socket(path: str, timeout: float = 2.0) -> socket.socket:
    """Connect to the helper, retrying until ``timeout`` seconds have passed."""
    deadline = time.monotonic() + timeout
    last: OSError | None = None
    while True:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(0.2)
            sock.connect(path)
            sock.settimeout(0.5)
            return sock
        except OSError as exc:
            last = exc
            try:
                sock.close()
            except OSError:
                pass
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
    assert last is not None
    raise last
