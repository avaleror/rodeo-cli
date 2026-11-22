"""JSON-RPC 2.0 over stdio — the MCP stdio transport, newline-delimited.

No third-party dependency: one frame per line in, one per line out. Requests
without an ``id`` are notifications and get no reply.

Each ``tools/call`` runs in its own thread so the stdin loop keeps reading
while a long command runs; that is how ``notifications/cancelled`` reaches a
call in flight. Replies are written under a lock so frames never interleave.

Cancel semantics: MCP lets a server send no response to a cancelled request.
This server does send one: the cancelled call's process group is killed and
the call gets a tool result with ``isError: true`` whose text starts with
``cancelled``. A client that already forgot the id drops it; one that did not
learns the command was stopped rather than waiting forever.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import IO

from .. import __version__
from .tools import call, tool_map, warn_if_no_allowlist

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
        self._inflight: dict = {}
        self._lock = threading.Lock()

    # --- calls in flight ----------------------------------------------------

    def track(self, req_id) -> threading.Event:
        """Register ``req_id`` as in flight; the returned event cancels it."""
        event = threading.Event()
        with self._lock:
            self._inflight[_key(req_id)] = event
        return event

    def untrack(self, req_id, event: threading.Event) -> None:
        with self._lock:
            if self._inflight.get(_key(req_id)) is event:
                del self._inflight[_key(req_id)]

    def cancel(self, req_id) -> bool:
        """Cancel the in-flight call ``req_id``. False when no such call runs."""
        with self._lock:
            event = self._inflight.get(_key(req_id))
        if event is None:
            return False
        event.set()
        return True

    def cancel_all(self) -> None:
        with self._lock:
            events = list(self._inflight.values())
        for event in events:
            event.set()

    # --- dispatch -------------------------------------------------------------

    def handle(self, message: dict, *, cancel: threading.Event | None = None) -> dict | None:
        """The reply for ``message``, or None when it needs none (a notification)."""
        req_id = message.get("id")
        method = message.get("method")
        if not isinstance(method, str):
            return _error(req_id, INVALID_REQUEST, "missing method") if req_id is not None else None
        params = message.get("params") or {}

        if method == "initialize":
            return _result(req_id, self._initialize(params))
        if method == "notifications/cancelled":
            if isinstance(params, dict) and "requestId" in params:
                self.cancel(params["requestId"])
            return None
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return _result(req_id, {})
        if method == "tools/list":
            return _result(req_id, {"tools": [t.descriptor() for t in self.tools.values()]})
        if method == "tools/call":
            return self._call(req_id, params, cancel)
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

    def _call(self, req_id, params: dict, cancel: threading.Event | None = None) -> dict:
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
            ok, text = call(tool, arguments, lab_dir=self.lab_dir, cancel=cancel)
        except Exception as exc:  # a tool failure is a result, not a transport error
            return _result(req_id, _text(f"{type(exc).__name__}: {exc}", is_error=True))
        if cancel is not None and cancel.is_set():
            return _result(req_id, _text(
                f"cancelled by the client; the command's process group was stopped.\n\n{text}",
                is_error=True))
        return _result(req_id, _text(text, is_error=not ok))


def _key(req_id) -> str:
    """Map key for a request id; JSON-RPC ids are strings or numbers."""
    return json.dumps(req_id)


#: Seconds serve() waits at EOF for cancelled calls to wind down.
EOF_JOIN_TIMEOUT = 30.0


def serve(
    *,
    allow_write: bool = False,
    lab_dir: Path | None = None,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
) -> int:
    """Read frames until EOF, dispatch each one, write each reply. Returns an exit code.

    ``tools/call`` requests run on worker threads; everything else is answered
    inline. At EOF every call still in flight is cancelled (its process group
    killed) before this returns.
    """
    reader = stdin or sys.stdin
    writer = stdout or sys.stdout
    warn_if_no_allowlist()
    server = RodeoMCPServer(allow_write=allow_write, lab_dir=lab_dir)
    write_lock = threading.Lock()
    workers: list[threading.Thread] = []

    def write(reply: dict | None) -> None:
        if reply is None:
            return
        with write_lock:
            try:
                writer.write(json.dumps(reply) + "\n")
                writer.flush()
            except (BrokenPipeError, ValueError):  # client gone or stream closed
                pass

    def run_call(message: dict, event: threading.Event) -> None:
        try:
            write(server.handle(message, cancel=event))
        finally:
            server.untrack(message.get("id"), event)

    for line in reader:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            write(_error(None, PARSE_ERROR, f"invalid JSON: {exc}"))
            continue
        if not isinstance(message, dict):
            write(_error(None, INVALID_REQUEST, "frame must be a JSON object"))
            continue
        if message.get("method") == "tools/call" and message.get("id") is not None:
            # Track before the thread starts so a cancel right behind it finds the call.
            event = server.track(message["id"])
            worker = threading.Thread(target=run_call, args=(message, event), daemon=True)
            workers.append(worker)
            worker.start()
            workers = [w for w in workers if w.is_alive()]
            continue
        write(server.handle(message))

    server.cancel_all()
    for worker in workers:
        worker.join(EOF_JOIN_TIMEOUT)
    return 0
