"""rodeo lab: the labs MCP tools may touch (~/.rodeo/labs.yaml).

    rodeo lab add <path>      # allow MCP tools in this lab (needs a terminal)
    rodeo lab list
    rodeo lab remove <path>   # needs a terminal
"""
from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.markup import escape

from ..lab_allowlist import add_lab, allowlist_path, load_labs, remove_lab

console = Console()

_NOTE = (
    "The list is ordinary user data: it stops an MCP client or a confused model from "
    "pointing tools at other directories, not a process running as you."
)


def _interactive() -> bool:
    return sys.stdin.isatty()


def _require_tty(action: str) -> None:
    if not _interactive():
        console.print(f"[red]✗  rodeo lab {action} needs a terminal: a person edits this list, "
                      "not an agent or a script.[/red]")
        raise SystemExit(2)


@click.group("lab")
def lab_cmd() -> None:
    """Labs MCP tools may use. Once the list exists, every MCP path
    argument must be inside a listed lab; without it any directory is accepted.

    The list is ordinary user data (a mistake filter for MCP clients and
    confused models), not a boundary against a process running as you.
    """


@lab_cmd.command("add")
@click.argument("path", type=click.Path(file_okay=False, dir_okay=True, exists=True))
def lab_add_cmd(path: str) -> None:
    """Allow MCP tools in PATH (stored as its realpath)."""
    _require_tty("add")
    first = load_labs() is None
    real = add_lab(path)
    console.print(f"[green]✓[/green]  {escape(real)} added to {allowlist_path()}")
    if first:
        console.print("[yellow]MCP tools now refuse paths outside the listed labs.[/yellow]")


@lab_cmd.command("remove")
@click.argument("path")
def lab_remove_cmd(path: str) -> None:
    """Stop allowing MCP tools in PATH. The file stays, even when empty."""
    _require_tty("remove")
    if remove_lab(path):
        console.print(f"[green]✓[/green]  {escape(path)} removed.")
    else:
        console.print(f"{escape(path)} was not listed.")


@lab_cmd.command("list")
def lab_list_cmd() -> None:
    """Listed labs."""
    labs = load_labs()
    if labs is None:
        console.print(f"No {allowlist_path()}: MCP tools accept any directory. "
                      "Run rodeo lab add <path> to start a list.")
        return
    for lab in labs:
        console.print(escape(lab))
    if not labs:
        console.print("(empty: MCP tools refuse every lab path)")
    console.print(f"[dim]{_NOTE}[/dim]")
