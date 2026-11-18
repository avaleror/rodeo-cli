"""JSON-RPC 2.0 over stdio — the MCP stdio transport, newline-delimited.

No third-party dependency: one frame per line in, one per line out. Requests
without an ``id`` are notifications and get no reply.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import IO

from .. import __version__
from .tools import call, tool_map

#: Protocol revisions this server implements; the newest is its preference.
SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
SERVER_NAME = "rodeo"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


def _result(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _text(content: str, *, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": content}], "isError": is_error}


class RodeoMCPServer:
    """Dispatches MCP methods onto the rodeo tool surface."""

    def __init__(self, *, allow_write: bool = False, lab_dir: Path | None = None) -> None:
        self.allow_write = allow_write
        self.lab_dir = lab_dir
        self.tools = tool_map(allow_write=allow_write)

    def handle(self, message: dict) -> dict | None:
        """The reply for ``message``, or None when it needs none (a notification)."""
        req_id = message.get("id")
        method = message.get("method")
        if not isinstance(method, str):
            return _error(req_id, INVALID_REQUEST, "missing method") if req_id is not None else None
        params = message.get("params") or {}

        if method == "initialize":
            return _result(req_id, self._initialize(params))
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None
        if method == "ping":
            return _result(req_id, {})
        if method == "tools/list":
            return _result(req_id, {"tools": [t.descriptor() for t in self.tools.values()]})
        if method == "tools/call":
            return self._call(req_id, params)
        if req_id is None:
            return None
        return _error(req_id, METHOD_NOT_FOUND, f"unknown method '{method}'")

    def _initialize(self, params: dict) -> dict:
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[-1]
        mode = "read-write" if self.allow_write else "read-only"
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            "instructions": (
                f"rodeo lab automation ({mode}). Tools wrap the rodeo CLI, so they cover "
                "native phase pipelines and external engines (lab-in-a-box) alike. "
                "Call rodeo_engines for the lab types available, rodeo_help for any "
                "subcommand without a tool of its own."
                + ("" if self.allow_write else " Lifecycle tools are disabled: "
                   "start the server with --allow-write to deploy or destroy labs.")
            ),
        }

    def _call(self, req_id, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(req_id, INVALID_PARAMS, "arguments must be an object")
        tool = self.tools.get(name)
        if tool is None:
            known = ", ".join(self.tools)
            hint = f"unknown tool '{name}'. Available: {known}"
            return _result(req_id, _text(hint, is_error=True))
        missing = [a.name for a in tool.args if a.required and not arguments.get(a.name)]
        if missing:
            return _result(req_id, _text(
                f"missing required argument(s): {', '.join(missing)}", is_error=True))
        try:
            ok, text = call(tool, arguments, lab_dir=self.lab_dir)
        except Exception as exc:  # a tool failure is a result, not a transport error
            return _result(req_id, _text(f"{type(exc).__name__}: {exc}", is_error=True))
        return _result(req_id, _text(text, is_error=not ok))


def serve(
    *,
    allow_write: bool = False,
    lab_dir: Path | None = None,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
) -> int:
    """Read frames until EOF, dispatch each one, write each reply. Returns an exit code."""
    reader = stdin or sys.stdin
    writer = stdout or sys.stdout
    server = RodeoMCPServer(allow_write=allow_write, lab_dir=lab_dir)

    for line in reader:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            reply = _error(None, PARSE_ERROR, f"invalid JSON: {exc}")
        else:
            if not isinstance(message, dict):
                reply = _error(None, INVALID_REQUEST, "frame must be a JSON object")
            else:
                reply = server.handle(message)
        if reply is None:
            continue
        writer.write(json.dumps(reply) + "\n")
        writer.flush()
    return 0
