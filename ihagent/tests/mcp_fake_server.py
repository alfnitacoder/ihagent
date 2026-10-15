"""Minimal stdio MCP server for IHAgent client tests.

Speaks newline-delimited JSON-RPC. The client uses the official SDK, so this
process only has to answer initialize, tools/list, and tools/call.

FAKE_MCP_ID       text included in echo results
FAKE_MCP_CRASH    ``once`` (first tools/call exits) or ``always``
FAKE_MCP_CRASH_FILE  remembers that the one-shot crash already happened
"""

from __future__ import annotations

import json
import os
import sys


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def tools() -> list[dict]:
    return [
        {
            "name": "echo",
            "description": "Echo text",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "lookup",
            "description": "Look something up",
            "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "boom",
            "description": "Always fails",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "portrait",
            "description": "Return an image",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "send_note",
            "description": "Send a note",
            "inputSchema": {
                "type": "object",
                "properties": {"body": {"type": "string"}},
                "required": ["body"],
            },
        },
    ]


def maybe_crash() -> None:
    mode = os.environ.get("FAKE_MCP_CRASH", "")
    if mode == "always":
        os._exit(1)
    if mode != "once":
        return
    path = os.environ.get("FAKE_MCP_CRASH_FILE", "")
    if not path:
        os._exit(1)
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("crashed\n")
    os._exit(1)


def call(params: dict) -> dict:
    maybe_crash()
    name = str((params or {}).get("name") or "")
    args = (params or {}).get("arguments") or {}
    if not isinstance(args, dict):
        args = {}
    ident = os.environ.get("FAKE_MCP_ID", "fake")
    if name == "echo":
        text = str(args.get("text", ""))
        return {"content": [{"type": "text", "text": f"echo:{ident}:{text}"}], "isError": False}
    if name == "lookup":
        return {"content": [{"type": "text", "text": f"found:{args.get('q', '')}"}], "isError": False}
    if name == "boom":
        return {"content": [{"type": "text", "text": "nope"}], "isError": True}
    if name == "portrait":
        return {
            "content": [{"type": "image", "data": "aGk=", "mimeType": "image/png"}],
            "isError": False,
        }
    if name == "send_note":
        return {
            "content": [{"type": "text", "text": f"sent:{args.get('body', '')}"}],
            "isError": False,
        }
    return {
        "content": [{"type": "text", "text": f"unknown tool {name}"}],
        "isError": True,
    }


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = message.get("method")
        mid = message.get("id")
        if method == "initialize" and mid is not None:
            version = (message.get("params") or {}).get("protocolVersion") or "2025-11-25"
            send(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {
                        "protocolVersion": version,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake", "version": "0"},
                    },
                }
            )
            continue
        if isinstance(method, str) and method.startswith("notifications/"):
            continue
        if mid is None:
            continue
        if method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools()}})
            continue
        if method == "tools/call":
            send({"jsonrpc": "2.0", "id": mid, "result": call(message.get("params") or {})})
            continue
        if method == "ping":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
            continue
        send(
            {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32601, "message": f"unknown method {method}"},
            }
        )


if __name__ == "__main__":
    main()
