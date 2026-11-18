"""``rodeo mcp`` — serve the rodeo tool surface over the MCP stdio transport."""
from __future__ import annotations

from pathlib import Path

import click

from ..config import find_lab_dir
from ..mcp.server import serve
from ..mcp.tools import READ_TOOLS, WRITE_TOOLS


@click.command("mcp", short_help="Serve rodeo as an MCP server on stdio.")
@click.option("--allow-write", is_flag=True,
              help="Also offer the lifecycle tools (up, deploy, clean, destroy, ...). "
                   "Off by default: without it the server is read-only.")
@click.option("--lab-dir", "lab_dir", default=None, metavar="DIR",
              type=click.Path(file_okay=False, dir_okay=True),
              help="Default lab directory for lab-scoped tools "
                   "(default: the detected lab dir, else the working directory).")
@click.option("--list-tools", is_flag=True,
              help="Print the tools this server would offer and exit.")
def mcp_cmd(allow_write: bool, lab_dir: str | None, list_tools: bool) -> None:
    """Expose rodeo to MCP clients (Claude Code, editors, agents) over stdio.

    Every tool runs the rodeo CLI, so native phase pipelines and external
    engines (lab-in-a-box, plugin engines) are covered by the same surface.

    \b
      rodeo mcp --list-tools                 # what is offered
      rodeo mcp                              # read-only server
      rodeo mcp --allow-write --lab-dir ~/labs/harvester
    """
    if list_tools:
        for tool in list(READ_TOOLS) + list(WRITE_TOOLS):
            if tool.mutating and not allow_write:
                continue
            kind = "write" if tool.mutating else "read"
            click.echo(f"{kind:5}  {tool.name}")
        return

    target = Path(lab_dir) if lab_dir else find_lab_dir()
    raise SystemExit(serve(allow_write=allow_write, lab_dir=target))
