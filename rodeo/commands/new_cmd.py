"""rodeo new — scaffold a custom, editable rodeo you can run with 'rodeo up'.

The declarative authoring loop:

  rodeo new mylab --from harvester     # copy a working lab into ~/.rodeo/profiles/mylab
  $EDITOR ~/.rodeo/profiles/mylab/definition.yaml   # change nodes, network, resources
  rodeo up --profile mylab             # deploy your edited lab

A profile is just a config-dir: a declarative ``definition.yaml`` (the topology) plus
a ``rodeo-plan.yaml`` (type, resources, credentials). See docs/custom-rodeos.md.
"""
from __future__ import annotations

import click
from rich.console import Console

from ..labseed import PROFILE_EXAMPLE, scaffold_profile

console = Console()


@click.command("new",
               short_help="Scaffold a custom rodeo you can edit, then 'rodeo up --profile <name>'.")
@click.argument("name")
@click.option("--from", "from_base", default="harvester",
              type=click.Choice(list(PROFILE_EXAMPLE)),
              help="Working lab to copy as the starting point (default: harvester).")
@click.option("--force", is_flag=True, help="Overwrite an existing custom profile of this name.")
def new_cmd(name: str, from_base: str, force: bool) -> None:
    """Create an editable custom rodeo named NAME under ~/.rodeo/profiles/."""
    try:
        dest = scaffold_profile(name, from_base=from_base, force=force)
    except FileExistsError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)
    except FileNotFoundError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    console.print(f"\n[bold green]Created profile '{name}'[/bold green] (from '{from_base}') at:")
    console.print(f"  [cyan]{dest}[/cyan]\n")
    console.print("[bold]Edit the topology[/bold] (nodes, network, resources, exposed services):")
    console.print(f"  {dest / 'definition.yaml'}")
    console.print(f"  {dest / 'rodeo-plan.yaml'}\n")
    console.print("[bold]Then deploy it:[/bold]")
    console.print(f"  rodeo up --profile {name}\n")
    console.print("[dim]Format and how-to: docs/custom-rodeos.md · List all: rodeo profiles[/dim]")
