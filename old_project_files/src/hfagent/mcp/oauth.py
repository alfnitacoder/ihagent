"""MCP OAuth for remote HTTP servers.

Discovery, dynamic client registration, PKCE, and refresh are the official
SDK's job. This module stores tokens under ~/.hfagent/mcp-tokens (0600) and
catches the browser redirect on 127.0.0.1.
"""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import token_dir


class AuthRequired(Exception):
    """The server wants a user login and this connect is not interactive."""


class FileTokenStorage:
    """SDK TokenStorage backed by one JSON file per server."""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def get_tokens(self) -> Any:
        from mcp.shared.auth import OAuthToken

        raw = self._read().get("tokens")
        if not isinstance(raw, dict) or not raw.get("access_token"):
            return None
        return OAuthToken.model_validate(raw)

    async def set_tokens(self, tokens: Any) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._write(data)

    async def get_client_info(self) -> Any:
        from mcp.shared.auth import OAuthClientInformationFull

        raw = self._read().get("client_info")
        if not isinstance(raw, dict) or not raw.get("client_id"):
            return None
        return OAuthClientInformationFull.model_validate(raw)

    async def set_client_info(self, client_info: Any) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)

    def has_tokens(self) -> bool:
        raw = self._read().get("tokens")
        return isinstance(raw, dict) and bool(raw.get("access_token"))

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        text = json.dumps(data, indent=2) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, text.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(self.path, 0o600)


def storage_for(name: str, directory: Path | None = None) -> FileTokenStorage:
    root = token_dir() if directory is None else directory
    return FileTokenStorage(root / f"{name}.json")


class LoopbackCallback:
    """Local /callback that the browser is redirected to after consent."""

    def __init__(self, timeout: float = 240.0) -> None:
        self.timeout = timeout
        self.port = 0
        self._queue: Queue[tuple[str, str, str | None, str]] = Queue()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}/callback"

    def start(self) -> None:
        if self._httpd is not None:
            return
        queue = self._queue

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/callback":
                    body = b"not found"
                    self.send_response(404)
                else:
                    qs = parse_qs(parsed.query)
                    code = (qs.get("code") or [""])[0]
                    state = (qs.get("state") or [""])[0]
                    iss = (qs.get("iss") or [None])[0]
                    error = (qs.get("error") or [""])[0]
                    queue.put((code, state, iss, error))
                    if error or not code:
                        body = f"IHAgent sign-in failed: {error or 'missing code'}. You can close this tab.".encode()
                    else:
                        body = b"IHAgent signed in. You can close this tab."
                    self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        httpd.daemon_threads = True
        self._httpd = httpd
        self.port = int(httpd.server_address[1])
        thread = threading.Thread(target=httpd.serve_forever, name="ihagent-mcp-oauth", daemon=True)
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    async def wait_result(self) -> Any:
        from mcp.shared.auth import AuthorizationCodeResult

        import asyncio

        def _get() -> tuple[str, str, str | None, str]:
            return self._queue.get(timeout=self.timeout)

        try:
            code, state, iss, error = await asyncio.to_thread(_get)
        except Empty as exc:
            raise TimeoutError("timed out waiting for the browser sign-in") from exc
        if error or not code:
            raise RuntimeError(error or "the redirect did not include a code")
        return AuthorizationCodeResult(code=code, state=state or None, iss=iss)


def build_provider(
    server_url: str,
    storage: FileTokenStorage,
    callback: LoopbackCallback,
    *,
    scope: str = "",
    interactive: bool,
    on_url: Any = None,
) -> Any:
    """OAuthClientProvider. PKCE and discovery stay inside the SDK."""
    from pydantic import AnyUrl

    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    metadata = OAuthClientMetadata(
        client_name="IHAgent",
        redirect_uris=[AnyUrl(callback.redirect_uri)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope=scope or None,
    )

    async def redirect_handler(url: str) -> None:
        if not interactive:
            raise AuthRequired("sign-in required")
        if on_url is not None:
            on_url(url)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    async def callback_handler() -> Any:
        if not interactive:
            raise AuthRequired("sign-in required")
        return await callback.wait_result()

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
