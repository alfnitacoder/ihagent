"""Background MCP sessions and the tools they add to the registry.

Connections run on one daemon thread with an asyncio loop. The agent loop
stays synchronous: a tool call waits on that thread and always returns a
string, including when a server crashes or a tool errors.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..tools.base import Tool, ToolRegistry
from .config import (
    ServerConfig,
    load_servers,
    log_dir,
    mcp_disabled,
    save_servers,
    token_dir,
)
from .oauth import AuthRequired, LoopbackCallback, build_provider, storage_for

_MUTATING = re.compile(
    r"(?:^|[_\s])(?:"
    r"write|send|delete|remove|trash|erase|destroy|drop|create|update|"
    r"reply|publish|share|upload|move|rename|insert|append|patch|modify|"
    r"edit|forward|compose|draft|put|add|set"
    r")(?:_|$)",
    re.IGNORECASE,
)
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def exposed_name(server: str, tool: str) -> str:
    """``server__tool``, safe for an OpenAI function name (max 64)."""
    server_s = _UNSAFE.sub("_", server).strip("_") or "mcp"
    tool_s = _UNSAFE.sub("_", tool).strip("_") or "tool"
    if not server_s[0].isalpha():
        server_s = "m" + server_s
    name = f"{server_s}__{tool_s}"
    if len(name) <= 64:
        return name
    digest = hashlib.sha256(f"{server}\0{tool}".encode()).hexdigest()[:6]
    prefix = server_s[:16]
    room = 64 - len(prefix) - 2 - len(digest) - 1
    tail = tool_s[-room:] if room > 0 else ""
    if tail:
        return f"{prefix}__{digest}_{tail}"[:64]
    return f"{prefix}__{digest}"[:64]


def unique_name(base: str, used: set[str]) -> str:
    if base not in used:
        return base
    index = 2
    while True:
        suffix = f"_{index}"
        candidate = base[: 64 - len(suffix)] + suffix
        if candidate not in used:
            return candidate
        index += 1


def approval_required(
    remote_name: str,
    *,
    read_only: bool,
    destructive: bool,
    auto_approve: set[str],
    exposed: str = "",
) -> bool:
    """Read-only hints may auto-run. Writes, sends, and deletes ask y/N.

    ``auto_approve`` (the server's list, or ``*``) skips the prompt.
    """
    if "*" in auto_approve or remote_name in auto_approve or (exposed and exposed in auto_approve):
        return False
    if _MUTATING.search(remote_name.replace("-", "_")):
        return True
    if destructive:
        return True
    if read_only:
        return False
    return True


def select_tools(tools: list[Any], config: ServerConfig) -> list[Any]:
    chosen = list(tools)
    if config.tools is not None:
        allow = set(config.tools)
        chosen = [tool for tool in chosen if getattr(tool, "name", None) in allow]
    chosen.sort(key=lambda tool: getattr(tool, "name", ""))
    if config.max_tools is not None:
        chosen = chosen[: config.max_tools]
    return chosen


def parameters_of(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "input_schema", None)
    if schema is None:
        schema = getattr(tool, "inputSchema", None)
    if hasattr(schema, "model_dump"):
        schema = schema.model_dump(by_alias=True, exclude_none=True)
    if not isinstance(schema, dict):
        schema = {}
    out = dict(schema)
    out.pop("$schema", None)
    out["type"] = "object"
    if not isinstance(out.get("properties"), dict):
        out["properties"] = {}
    return out


def format_call_result(result: Any) -> str:
    """Text for the model. Non-text blocks are a short summary, not raw bytes."""
    is_error = bool(getattr(result, "is_error", False))
    if isinstance(result, dict):
        is_error = bool(result.get("isError") or result.get("is_error"))
    blocks = getattr(result, "content", None)
    if blocks is None and isinstance(result, dict):
        blocks = result.get("content") or []
    text_parts: list[str] = []
    other: list[str] = []
    for block in blocks or []:
        kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if kind == "text":
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", "")
            text_parts.append(str(text or ""))
        else:
            other.append(_summarize_block(block, kind))
    body = "\n".join(part for part in (*text_parts, *other) if part)
    structured = getattr(result, "structured_content", None)
    if structured is None and isinstance(result, dict):
        structured = result.get("structuredContent", result.get("structured_content"))
    if structured is not None:
        if not text_parts:
            blob = json.dumps(structured, default=str)
            if len(blob) > 4000:
                blob = blob[:4000] + "… [truncated structured content]"
            body = (body + "\n" if body else "") + blob
        elif isinstance(structured, dict) and structured:
            keys = ", ".join(list(structured)[:12])
            body = (body + "\n" if body else "") + f"[structured keys: {keys}]"
    body = body.strip()
    if is_error:
        if not body:
            body = "tool returned an error"
        if not body.startswith("Error:"):
            body = "Error: " + body
        return body
    return body or "(empty tool result)"


def _summarize_block(block: Any, kind: str | None) -> str:
    def get(name: str, default: str = "") -> str:
        if isinstance(block, dict):
            return str(block.get(name, default) or default)
        return str(getattr(block, name, default) or default)

    if kind == "image":
        return f"[image {get('mime_type', get('mimeType', 'unknown'))}, {len(get('data'))} base64 chars]"
    if kind == "audio":
        return f"[audio {get('mime_type', get('mimeType', 'unknown'))}, {len(get('data'))} base64 chars]"
    if kind == "resource":
        resource = block.get("resource") if isinstance(block, dict) else getattr(block, "resource", None)
        uri = ""
        text = ""
        mime = "binary"
        blob = ""
        if isinstance(resource, dict):
            uri = str(resource.get("uri") or "")
            text = str(resource.get("text") or "")
            mime = str(resource.get("mimeType") or resource.get("mime_type") or mime)
            blob = str(resource.get("blob") or "")
        elif resource is not None:
            uri = str(getattr(resource, "uri", "") or "")
            text = str(getattr(resource, "text", "") or "")
            mime = str(getattr(resource, "mime_type", "") or mime)
            blob = str(getattr(resource, "blob", "") or "")
        if text:
            return f"[resource {uri}]\n{text}"
        return f"[resource {uri}, {mime}, {len(blob)} base64 chars]"
    if kind == "resource_link":
        return f"[resource link {get('uri')} {get('name')}]".strip()
    return f"[{kind or 'content'}]"


def suggests_sse(exc: BaseException) -> bool:
    """True when Streamable HTTP was refused in a way SSE might accept."""
    code = _status_code(exc)
    if code in {404, 405, 406, 415}:
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    needles = (
        "text/event-stream",
        "not acceptable",
        "method not allowed",
        "unsupported media type",
    )
    return any(needle in text for needle in needles)


def is_auth_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    for _ in range(5):
        if current is None:
            break
        if isinstance(current, AuthRequired):
            return True
        if type(current).__name__ in {"OAuthFlowError", "OAuthRegistrationError", "OAuthTokenError"}:
            return True
        code = _status_code(current)
        if code in {401, 403}:
            return True
        text = str(current).lower()
        if "unauthorized" in text or " 401" in text or text.startswith("401"):
            return True
        current = current.__cause__ or current.__context__
    return False


def _status_code(exc: BaseException) -> int | None:
    seen: list[BaseException | None] = [exc, exc.__cause__, exc.__context__]
    for obj in seen:
        if obj is None:
            continue
        code = getattr(obj, "status_code", None)
        if isinstance(code, int):
            return code
        response = getattr(obj, "response", None)
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def _note_working(_name: str) -> None:
    """Tell iheye a tool is running. The bot skin lights its chest; other skins ignore this flag."""
    try:
        from ..iheye import publish_activity

        publish_activity(working=True)
    except Exception:
        return


class McpTool(Tool):
    """One remote MCP tool, registered under a namespaced name."""

    def __init__(
        self,
        hub: "McpHub",
        server: str,
        remote_name: str,
        exposed: str,
        description: str,
        parameters: dict[str, Any],
        needs_approval: bool,
        call_timeout: float,
    ) -> None:
        self._hub = hub
        self._server = server
        self._remote = remote_name
        self.name = exposed
        self.description = description
        self.parameters = parameters
        self.needs_approval = needs_approval
        self._call_timeout = call_timeout

    def preview(self, **kwargs: Any) -> str:
        try:
            body = json.dumps(kwargs, ensure_ascii=False, default=str)
        except TypeError:
            body = str(kwargs)
        if len(body) > 800:
            body = body[:800] + "…"
        return f"{self._server} · {self._remote}\n{body}"

    def run(self, **kwargs: Any) -> str:
        _note_working(self.name)
        try:
            return self._hub.call_sync(self._server, self._remote, kwargs, self._call_timeout)
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"


@dataclass
class ServerState:
    name: str
    status: str
    detail: str = ""
    tool_count: int = 0
    server_tool_count: int = 0
    exposed: list[str] = field(default_factory=list)
    kind: str = ""


@dataclass
class _Conn:
    client: Any
    closer: Any


class McpHub:
    """Owns MCP connections for one agent registry."""

    def __init__(
        self,
        registry: ToolRegistry,
        ui_getter: Any,
        *,
        config_file: Any = None,
        tokens: Any = None,
    ) -> None:
        from .config import config_path

        self.registry = registry
        self._ui_getter = ui_getter
        self.config_file = config_path() if config_file is None else config_file
        self.token_dir = token_dir() if tokens is None else tokens
        self._lock = threading.Lock()
        self._states: dict[str, ServerState] = {}
        self._exposed: dict[str, list[str]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = False
        self._pending = False
        self._conns: dict[str, _Conn] = {}
        self._server_locks: dict[str, asyncio.Lock] = {}

    def states(self) -> list[ServerState]:
        with self._lock:
            return list(self._states.values())

    def start(self) -> None:
        if mcp_disabled():
            return
        with self._lock:
            if self._started:
                return
            self._started = True
            self._pending = True
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(loop)
            loop.call_soon(ready.set)
            loop.run_forever()

        self._loop = loop
        thread = threading.Thread(target=run, name="ihagent-mcp", daemon=True)
        self._thread = thread
        thread.start()
        ready.wait(5)
        self._submit(self._boot())

    def ensure_started(self) -> None:
        self.start()

    def stop(self) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
            fut.result(timeout=5)
        except Exception:
            pass
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3)
        with self._lock:
            self._started = False
        self._loop = None
        self._thread = None

    def connect_now(self, name: str, timeout: float = 30.0) -> str:
        self.ensure_started()
        loop = self._loop
        if loop is None:
            return "MCP client is not running"
        try:
            servers = load_servers(self.config_file)
        except Exception as exc:
            return f"Error: {exc}"
        cfg = servers.get(name)
        if cfg is None:
            return f"no MCP server named {name}"
        if not cfg.enabled:
            fut = asyncio.run_coroutine_threadsafe(
                self._with_server(name, lambda: self._close_locked(name)),
                loop,
            )
            try:
                fut.result(timeout=10)
            except Exception as exc:
                return f"Error: {exc}"
            self._set_state(ServerState(name=name, status="disabled", kind=_kind(cfg)))
            self._tools_changed()
            return f"{name} disabled"
        fut = asyncio.run_coroutine_threadsafe(
            self._with_server(name, lambda: self._connect_locked(cfg, interactive=False)),
            loop,
        )
        try:
            return str(fut.result(timeout=timeout))
        except Exception as exc:
            return f"Error: {exc}"

    def disconnect(self, name: str) -> None:
        previous = self._exposed.get(name, [])
        if previous:
            self.registry.swap(previous, [])
            self._exposed[name] = []
        loop = self._loop
        if loop is not None and self._started:
            fut = asyncio.run_coroutine_threadsafe(
                self._with_server(name, lambda: self._close_locked(name)),
                loop,
            )
            try:
                fut.result(timeout=8)
            except Exception:
                pass
        self._drop_state(name)
        self._tools_changed()

    def reload_sync(self, timeout: float = 30.0) -> None:
        self.ensure_started()
        loop = self._loop
        if loop is None:
            return
        with self._lock:
            self._pending = True
        fut = asyncio.run_coroutine_threadsafe(self._boot(), loop)
        fut.result(timeout=timeout)

    def wait_settled(self, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                pending = self._pending
                connecting = any(state.status == "connecting" for state in self._states.values())
            if not pending and not connecting:
                return
            time.sleep(0.02)
        raise TimeoutError("MCP connect did not settle")

    def call_sync(self, server: str, tool: str, arguments: dict[str, Any], timeout: float) -> str:
        loop = self._loop
        if loop is None or not self._started:
            return f"Error: MCP server {server} is not connected"
        fut = asyncio.run_coroutine_threadsafe(self._call(server, tool, arguments), loop)
        try:
            return fut.result(timeout=timeout + 5)
        except TimeoutError:
            fut.cancel()
            return f"Error: MCP tool {server}__{tool} timed out after {timeout:.0f}s"
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"

    async def _boot(self) -> None:
        try:
            await self._reload()
        finally:
            with self._lock:
                self._pending = False

    async def _reload(self) -> None:
        try:
            servers = load_servers(self.config_file)
        except Exception as exc:
            self._notify(f"mcp config: {exc}")
            return
        known = set(self._conns) | set(self._states)
        for name in known:
            if name not in servers or not servers[name].enabled:
                await self._with_server(name, lambda name=name: self._close_locked(name))
                if name not in servers:
                    self._drop_state(name)
        await asyncio.gather(
            *(
                self._with_server(name, lambda name=name, cfg=cfg: self._connect_locked(cfg, interactive=False))
                for name, cfg in servers.items()
                if cfg.enabled
            ),
            return_exceptions=True,
        )
        self._tools_changed()

    async def _shutdown(self) -> None:
        names = list(self._conns)
        for name in names:
            await self._with_server(name, lambda name=name: self._close_locked(name))

    def login(self, name: str) -> str:
        try:
            servers = load_servers(self.config_file)
        except Exception as exc:
            return f"Error: {exc}"
        cfg = servers.get(name)
        if cfg is None:
            return f"no MCP server named {name}"
        if not cfg.remote:
            return ""
        cfg.oauth = True
        cfg.timeout = max(cfg.timeout, 300.0)
        servers[name] = cfg
        try:
            save_servers(servers, self.config_file)
        except Exception:
            pass
        self.ensure_started()
        loop = self._loop
        if loop is None:
            return "MCP client is not running"
        fut = asyncio.run_coroutine_threadsafe(
            self._with_server(name, lambda: self._connect_locked(cfg, interactive=True)),
            loop,
        )
        try:
            message = fut.result(timeout=cfg.timeout + 15)
        except Exception as exc:
            return f"Error: {exc}"
        return message or self._status_line(name)

    async def _call(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        async def attempt() -> str:
            return await self._call_locked(server, tool, arguments)

        return await self._with_server(server, attempt)

    async def _call_locked(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        conn = self._conns.get(server)
        if conn is None:
            await self._reconnect_locked(server)
            conn = self._conns.get(server)
        if conn is None:
            detail = self._detail(server)
            return f"Error: MCP server {server} is not connected{detail}"
        try:
            result = await conn.client.call_tool(tool, arguments or {})
            return format_call_result(result)
        except Exception as first:
            await self._close_locked(server)
            try:
                await self._reconnect_locked(server)
                conn = self._conns.get(server)
                if conn is None:
                    return f"Error: MCP server {server} failed ({_short(first)})"
                result = await conn.client.call_tool(tool, arguments or {})
                return format_call_result(result)
            except Exception as exc:
                return f"Error: MCP server {server} failed ({_short(exc)})"

    async def _reconnect_locked(self, server: str) -> None:
        try:
            servers = load_servers(self.config_file)
        except Exception:
            return
        cfg = servers.get(server)
        if cfg is None or not cfg.enabled:
            return
        await self._connect_locked(cfg, interactive=False)

    async def _connect_locked(self, cfg: ServerConfig, *, interactive: bool) -> str:
        await self._close_locked(cfg.name)
        self._set_state(ServerState(name=cfg.name, status="connecting", kind=_kind(cfg)))
        timeout = cfg.timeout if not interactive else max(cfg.timeout, 300.0)
        try:
            conn = await asyncio.wait_for(
                self._open(cfg, interactive=interactive),
                timeout=timeout,
            )
        except AuthRequired as exc:
            self._set_state(
                ServerState(
                    name=cfg.name,
                    status="login",
                    detail=str(exc) or "sign-in required",
                    kind=_kind(cfg),
                )
            )
            self._notify(f"mcp {cfg.name}: sign in with /mcp login {cfg.name}")
            return f"{cfg.name} needs /mcp login"
        except Exception as exc:
            self._set_state(
                ServerState(name=cfg.name, status="error", detail=_short(exc), kind=_kind(cfg))
            )
            self._notify(f"mcp {cfg.name}: {_short(exc)}")
            return f"{cfg.name} failed: {_short(exc)}"
        self._conns[cfg.name] = conn
        try:
            tools = await asyncio.wait_for(self._list_all(conn.client), timeout=cfg.timeout)
        except Exception as exc:
            await self._close_locked(cfg.name)
            self._set_state(
                ServerState(name=cfg.name, status="error", detail=_short(exc), kind=_kind(cfg))
            )
            self._notify(f"mcp {cfg.name}: {_short(exc)}")
            return f"{cfg.name} failed: {_short(exc)}"
        try:
            exposed = self._install(cfg, tools)
        except Exception as exc:
            await self._close_locked(cfg.name)
            self._set_state(
                ServerState(name=cfg.name, status="error", detail=_short(exc), kind=_kind(cfg))
            )
            return f"{cfg.name} failed: {_short(exc)}"
        self._set_state(
            ServerState(
                name=cfg.name,
                status="ready",
                detail="",
                tool_count=len(exposed),
                server_tool_count=len(tools),
                exposed=exposed,
                kind=_kind(cfg),
            )
        )
        self._notify(f"mcp {cfg.name}: {len(exposed)} tools")
        self._tools_changed()
        return f"{cfg.name} connected ({len(exposed)} tools)"

    async def _close_locked(self, name: str) -> None:
        conn = self._conns.pop(name, None)
        previous = self._exposed.get(name, [])
        if previous:
            self.registry.swap(previous, [])
            self._exposed[name] = []
        if conn is None:
            return
        try:
            await conn.closer()
        except Exception:
            pass

    def _drop_state(self, name: str) -> None:
        with self._lock:
            self._states.pop(name, None)
            self._exposed.pop(name, None)

    async def _open(self, cfg: ServerConfig, *, interactive: bool) -> _Conn:
        stack = contextlib.AsyncExitStack()
        try:
            if cfg.command:
                client = await self._enter_stdio(stack, cfg)
            else:
                client = await self._enter_remote(stack, cfg, interactive=interactive)
        except asyncio.CancelledError:
            await stack.aclose()
            raise
        except Exception:
            await stack.aclose()
            raise

        async def closer() -> None:
            await stack.aclose()

        return _Conn(client=client, closer=closer)

    async def _enter_stdio(self, stack: contextlib.AsyncExitStack, cfg: ServerConfig) -> Any:
        from mcp import Client, StdioServerParameters
        from mcp.client.stdio import stdio_client

        logs = log_dir()
        logs.mkdir(parents=True, exist_ok=True)
        path = logs / f"{cfg.name}.log"
        try:
            if path.is_file() and path.stat().st_size > 200_000:
                path.write_text("", encoding="utf-8")
            handle = open(path, "a", encoding="utf-8")
        except OSError:
            handle = open(os.devnull, "w", encoding="utf-8")
        stack.callback(handle.close)
        params = StdioServerParameters(
            command=cfg.command,
            args=list(cfg.args),
            env=dict(cfg.env) or None,
            cwd=cfg.cwd or None,
        )
        client = _make_client(stdio_client(params, errlog=handle), cfg)
        await stack.enter_async_context(client)
        return client

    async def _enter_remote(
        self,
        stack: contextlib.AsyncExitStack,
        cfg: ServerConfig,
        *,
        interactive: bool,
    ) -> Any:
        store = storage_for(cfg.name, self.token_dir)
        auth = None
        callback: LoopbackCallback | None = None
        if cfg.oauth or store.has_tokens():
            auth, callback = self._oauth(cfg, store, interactive=interactive)
            if callback is not None:
                stack.callback(callback.stop)
        try:
            client, nested = await self._remote_transport(cfg, auth)
        except AuthRequired:
            raise
        except Exception as exc:
            if is_auth_error(exc) and not interactive:
                raise AuthRequired("sign-in required") from exc
            if is_auth_error(exc) and auth is None:
                if not interactive:
                    raise AuthRequired("sign-in required") from exc
                auth, callback = self._oauth(cfg, store, interactive=True)
                if callback is not None:
                    stack.callback(callback.stop)
                try:
                    client, nested = await self._remote_transport(cfg, auth)
                except Exception as retry:
                    raise AuthRequired("sign-in failed") from retry
            else:
                raise
        stack.push_async_callback(nested.aclose)
        return client

    def _oauth(
        self,
        cfg: ServerConfig,
        store: Any,
        *,
        interactive: bool,
    ) -> tuple[Any, LoopbackCallback]:
        callback = LoopbackCallback()
        callback.start()

        def on_url(url: str) -> None:
            self._notify(f"mcp {cfg.name}: open this URL if the browser does not:\n{url}")

        provider = build_provider(
            cfg.url,
            store,
            callback,
            scope=cfg.oauth_scope,
            interactive=interactive,
            on_url=on_url,
        )
        return provider, callback

    async def _remote_transport(self, cfg: ServerConfig, auth: Any) -> tuple[Any, contextlib.AsyncExitStack]:
        mode = cfg.transport or "auto"
        if mode == "sse":
            return await self._attempt(lambda nested: self._open_sse(nested, cfg, auth))
        try:
            return await self._attempt(lambda nested: self._open_http(nested, cfg, auth))
        except Exception as exc:
            if mode == "http" or not suggests_sse(exc):
                raise
            return await self._attempt(lambda nested: self._open_sse(nested, cfg, auth))

    async def _attempt(self, factory: Any) -> tuple[Any, contextlib.AsyncExitStack]:
        nested = contextlib.AsyncExitStack()
        try:
            client = await factory(nested)
        except Exception:
            await nested.aclose()
            raise
        return client, nested

    async def _open_http(self, nested: contextlib.AsyncExitStack, cfg: ServerConfig, auth: Any) -> Any:
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        http = httpx2.AsyncClient(
            headers=dict(cfg.headers) or None,
            auth=auth,
            timeout=httpx2.Timeout(30.0, read=max(cfg.call_timeout, 60.0)),
        )
        await nested.enter_async_context(http)
        client = _make_client(streamable_http_client(cfg.url, http_client=http), cfg)
        await nested.enter_async_context(client)
        return client

    async def _open_sse(self, nested: contextlib.AsyncExitStack, cfg: ServerConfig, auth: Any) -> Any:
        from mcp.client.sse import sse_client

        transport = sse_client(
            cfg.url,
            headers=dict(cfg.headers) or None,
            timeout=min(cfg.timeout, 30.0),
            sse_read_timeout=max(cfg.call_timeout, 60.0),
            auth=auth,
        )
        client = _make_client(transport, cfg)
        await nested.enter_async_context(client)
        return client

    async def _list_all(self, client: Any) -> list[Any]:
        tools: list[Any] = []
        cursor = None
        for _ in range(50):
            page = await client.list_tools(cursor=cursor, cache_mode="refresh")
            tools.extend(page.tools)
            cursor = getattr(page, "next_cursor", None)
            if not cursor:
                break
        return tools

    def _install(self, cfg: ServerConfig, remote_tools: list[Any]) -> list[str]:
        previous = self._exposed.get(cfg.name, [])
        used = set(self.registry.names()) - set(previous)
        selected = select_tools(remote_tools, cfg)
        built: list[McpTool] = []
        names: list[str] = []
        auto = set(cfg.auto_approve)
        for tool in selected:
            annotations = getattr(tool, "annotations", None)
            read_only = bool(getattr(annotations, "read_only_hint", False))
            destructive = bool(getattr(annotations, "destructive_hint", False))
            remote_name = str(getattr(tool, "name", "") or "tool")
            base = exposed_name(cfg.name, remote_name)
            name = unique_name(base, used)
            used.add(name)
            needs = approval_required(
                remote_name,
                read_only=read_only,
                destructive=destructive,
                auto_approve=auto,
                exposed=name,
            )
            text = str(getattr(tool, "description", "") or remote_name).strip()
            if len(text) > 400:
                text = text[:400] + "…"
            mark = " Read-only." if not needs else " Requires user approval before it runs."
            built.append(
                McpTool(
                    self,
                    cfg.name,
                    remote_name,
                    name,
                    f"[mcp {cfg.name}] {text}{mark}",
                    parameters_of(tool),
                    needs,
                    cfg.call_timeout,
                )
            )
            names.append(name)
        self.registry.swap(previous, built)
        self._exposed[cfg.name] = names
        return names

    async def _with_server(self, name: str, fn: Any) -> Any:
        lock = self._server_locks.get(name)
        if lock is None:
            lock = asyncio.Lock()
            self._server_locks[name] = lock
        async with lock:
            return await fn()

    def _set_state(self, state: ServerState) -> None:
        with self._lock:
            self._states[state.name] = state

    def _detail(self, name: str) -> str:
        with self._lock:
            state = self._states.get(name)
        if state is None or not state.detail:
            return ""
        return f" ({state.detail})"

    def _status_line(self, name: str) -> str:
        with self._lock:
            state = self._states.get(name)
        if state is None:
            return name
        return f"{name} {state.status}" + (f" — {state.detail}" if state.detail else "")

    def _notify(self, text: str) -> None:
        ui = self._ui_getter() if self._ui_getter else None
        if ui is None:
            return
        try:
            ui.on_status(text)
        except Exception:
            return

    def _tools_changed(self) -> None:
        ui = self._ui_getter() if self._ui_getter else None
        if ui is None:
            return
        hook = getattr(ui, "on_tools_changed", None)
        if not callable(hook):
            return
        try:
            hook()
        except Exception:
            return

    def _submit(self, coro: Any) -> None:
        loop = self._loop
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(coro, loop)


def _make_client(transport: Any, cfg: ServerConfig) -> Any:
    from mcp import Client
    from mcp.types import Implementation

    from .. import __version__

    return Client(
        transport,
        mode="auto",
        read_timeout_seconds=cfg.call_timeout,
        client_info=Implementation(name="IHAgent", version=__version__),
    )


def _kind(cfg: ServerConfig) -> str:
    if cfg.command:
        return "stdio"
    if cfg.transport == "sse":
        return "sse"
    return "http"


def _short(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    text = " ".join(text.split())
    if len(text) > 180:
        text = text[:180] + "…"
    if type(exc).__name__ in text:
        return text
    return f"{type(exc).__name__}: {text}"
