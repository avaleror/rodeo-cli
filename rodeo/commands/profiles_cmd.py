"""rodeo profiles — list the rodeo profiles you can deploy with 'rodeo up --profile'."""
from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from ..labseed import list_profiles

console = Console()


@click.command("profiles", short_help="List rodeo profiles (bundled + your custom ones).")
def profiles_cmd() -> None:
    """Show every profile usable with 'rodeo up --profile <name>'."""
    rows = list_profiles()
    table = Table(title="Rodeo profiles")
    table.add_column("name", style="bold")
    table.add_column("kind")
    table.add_column("source", style="dim")
    for r in rows:
        kind = "[cyan]bundled[/cyan]" if r["kind"] == "bundled" else "[green]custom[/green]"
        table.add_row(r["name"], kind, r["path"])
    console.print()
    console.print(table)
    console.print("\n[dim]Deploy one:  rodeo up --profile <name>   ·   "
                  "Create one:  rodeo new <name> --from harvester[/dim]\n")
