"""rodeo trust — approve the ??cmd: and ??file: secret sources in a plan.

    rodeo trust          # review this lab's sources and approve them
    rodeo trust --list   # what is approved on this machine
"""
from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.markup import escape

from ..config import (
    collect_gated_sources,
    load_config,
    load_trusted_sources,
    save_trusted_sources,
)
from ._options import config_options

console = Console()


def _interactive() -> bool:
    return sys.stdin.isatty()


@click.command("trust")
@config_options
@click.option("--list", "list_only", is_flag=True, help="Show the approved sources and exit.")
def trust_cmd(
    config_path: str,
    config_dir: str | None,
    params: tuple[str, ...],
    paramfile: str | None,
    list_only: bool,
) -> None:
    """Approve the ??cmd: and ??file: secret sources this plan uses.

    rodeo never runs a ??cmd: command or reads a ??file: path from a plan until
    you approve that exact string here once. Needs an interactive terminal, so
    agents and MCP clients can't approve on your behalf.
    """
    trusted = load_trusted_sources()
    if list_only:
        if not trusted:
            console.print("No approved secret sources.")
        for source in sorted(trusted):
            console.print(escape(source))
        return

    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")
    cfg = load_config(
        config_path, params=params, paramfile=paramfile, config_dir=config_dir,
        resolve_secrets=False,
    )
    pending = sorted(collect_gated_sources(cfg) - trusted)
    if not pending:
        console.print("Nothing to approve: every ??cmd:/??file: source in this plan is approved.")
        return

    console.print("This plan wants to run or read:\n")
    for source in pending:
        console.print(f"  {escape(source)}")
    console.print(
        "\n[yellow]Approve only what you recognise. ??cmd: runs as you, through the shell.[/yellow]"
    )
    if not _interactive():
        raise click.ClickException("rodeo trust needs an interactive terminal.")
    if not click.confirm("Approve these sources?", default=False):
        raise click.ClickException("Nothing approved.")
    path = save_trusted_sources(trusted | set(pending))
    console.print(f"Approved {len(pending)} source(s) in {path}.")
