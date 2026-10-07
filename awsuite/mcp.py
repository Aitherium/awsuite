"""MCP stdio server: JSON-RPC 2.0, one JSON object per line.

Implements `initialize`, `notifications/initialized`, `ping`, `tools/list` and
`tools/call`. The tool list is `tools.mcp_tools()` -- the same table the CLI and
the awdk toolpack render -- so nothing here can drift from them.

    claude mcp add awsuite -- awsuite mcp

Stdout carries protocol frames ONLY; diagnostics go to stderr. A stray print on
stdout corrupts the stream for the client.
"""

from __future__ import annotations

import json
import sys
from typing import Any, BinaryIO, Callable, Dict, Optional

from . import __version__
from .tools import BY_NAME, Context, call_tool_safe, mcp_tools

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "awsuite gives you the user's Google Workspace: suite_mail_*, suite_drive_*, "
    "suite_calendar_*, suite_docs_*, suite_sheets_*, suite_directory_users. Read tools run "
    "directly. Write tools (send, draft, create, append, upload) return a dry-run preview "
    "unless you pass confirm=true -- show the preview to the user and only confirm when "
    "they have agreed. suite_auth_status says which account and scopes are active; a "
    "ScopeError names the scope to grant with `awsuite auth login --scopes ...`."
)


class Server:
    """A transport-free JSON-RPC handler; `serve()` wires it to byte streams."""

    def __init__(self, ctx_factory: Optional[Callable[[], Context]] = None) -> None:
        self.ctx_factory = ctx_factory or Context
        self.initialized = False

    @staticmethod
    def _result(rid: Any, result: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    @staticmethod
    def _error(rid: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

    def handle(self, msg: Any) -> Optional[Dict[str, Any]]:
        """One request in, one response out (None for notifications)."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return self._error(msg.get("id") if isinstance(msg, dict) else None,
                               -32600, "invalid request")
        method = msg.get("method")
        rid = msg.get("id")
        is_note = "id" not in msg
        params = msg.get("params") or {}
        if not isinstance(method, str):
            return None if is_note else self._error(rid, -32600, "missing method")

        if method == "initialize":
            asked = params.get("protocolVersion") if isinstance(params, dict) else None
            ver = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return self._result(rid, {
                "protocolVersion": ver,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "awsuite", "version": __version__},
                "instructions": INSTRUCTIONS,
            })
        if method == "notifications/initialized":
            self.initialized = True
            return None
        if method.startswith("notifications/"):
            return None
        if method == "ping":
            return None if is_note else self._result(rid, {})
        if method == "tools/list":
            return self._result(rid, {"tools": mcp_tools()})
        if method == "tools/call":
            if not isinstance(params, dict):
                return self._error(rid, -32602, "params must be an object")
            name = params.get("name")
            if name not in BY_NAME:
                return self._error(rid, -32602, f"unknown tool: {name}")
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                return self._error(rid, -32602, "arguments must be an object")
            try:
                out, is_err = call_tool_safe(name, args, self.ctx_factory())
            except Exception as exc:  # noqa: BLE001 - a tool bug must not kill the server
                out, is_err = {"ok": False, "error": type(exc).__name__,
                               "message": str(exc)}, True
            return self._result(rid, {
                "content": [{"type": "text",
                             "text": json.dumps(out, indent=2, default=str)}],
                "isError": is_err,
            })
        return None if is_note else self._error(rid, -32601, f"method not found: {method}")


def serve(stdin: BinaryIO, stdout: BinaryIO, server: Optional[Server] = None) -> int:
    """Read newline-delimited JSON-RPC from `stdin` until EOF; answer on `stdout`."""
    srv = server or Server()
    for raw in iter(stdin.readline, b""):
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            resp: Optional[Dict[str, Any]] = Server._error(None, -32700, "parse error")
        else:
            if isinstance(msg, list):
                answers = [a for a in (srv.handle(m) for m in msg) if a is not None]
                if answers:
                    stdout.write(json.dumps(answers).encode("utf-8") + b"\n")
                    stdout.flush()
                continue
            resp = srv.handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp).encode("utf-8") + b"\n")
            stdout.flush()
    return 0


def main(profile: Optional[str] = None) -> int:
    """Entry for `awsuite mcp`: serve on the process's stdio."""
    srv = Server(ctx_factory=lambda: Context(profile))
    print(f"awsuite {__version__} MCP server on stdio ({len(BY_NAME)} tools)", file=sys.stderr)
    return serve(sys.stdin.buffer, sys.stdout.buffer, srv)
