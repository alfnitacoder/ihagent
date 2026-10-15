"""MCP client: connect servers from ~/.hfagent/mcp.json and expose their tools."""

from __future__ import annotations

import atexit

from .commands import handle_mcp, hub_for
from .config import config_path, mcp_disabled, token_dir
from .session import McpHub

_HUBS: list[McpHub] = []
_ATEXIT = False


def start(agent: object) -> McpHub | None:
    """Connect configured servers on a background thread. Never raises."""
    if mcp_disabled():
        return None
    try:
        import mcp  # noqa: F401  official SDK
    except ImportError:
        _say(agent, "MCP support needs the mcp package (pip install 'mcp>=2')")
        return None
    hub = hub_for(agent)  # type: ignore[arg-type]
    _remember(hub)
    try:
        hub.start()
    except Exception as exc:
        _say(agent, f"mcp failed to start: {exc}")
    return hub


def stop() -> None:
    for hub in list(_HUBS):
        try:
            hub.stop()
        except Exception:
            pass
    _HUBS.clear()


def config_report_lines() -> list[str]:
    path = config_path()
    try:
        from .config import load_servers

        servers = load_servers(path)
        enabled = sum(1 for server in servers.values() if server.enabled)
        summary = f"{len(servers)} ({enabled} enabled)"
    except Exception as exc:
        summary = f"unreadable ({exc})"
    return [
        "",
        "MCP (stdio and remote HTTP servers, tools named server__tool):",
        f"mcp_config={path}",
        f"mcp_servers={summary}",
        f"mcp_tokens={token_dir()}",
    ]


def _remember(hub: McpHub) -> None:
    global _ATEXIT
    if hub not in _HUBS:
        _HUBS.append(hub)
    if not _ATEXIT:
        atexit.register(stop)
        _ATEXIT = True


def _say(agent: object, text: str) -> None:
    ui = getattr(agent, "ui", None)
    if ui is None:
        return
    try:
        ui.on_status(text)
    except Exception:
        return


__all__ = [
    "McpHub",
    "config_report_lines",
    "handle_mcp",
    "hub_for",
    "start",
    "stop",
]
