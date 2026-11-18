"""Model Context Protocol server for rodeo.

``rodeo mcp`` speaks JSON-RPC 2.0 over stdio (newline-delimited, the MCP stdio
transport) with no third-party dependency. The tool surface is declared in
:mod:`rodeo.mcp.tools` and executed by :mod:`rodeo.mcp.exec`, so every rodeo
subcommand an agent can call goes through the same CLI an operator uses —
native phase pipelines and external engines (lab-in-a-box) alike.
"""
from __future__ import annotations

from .server import serve

__all__ = ["serve"]
