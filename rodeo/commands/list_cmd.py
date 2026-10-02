"""rodeo list — plans and the VMs each owns on this host."""
from __future__ import annotations

import click
from rich.console import Console

from ..engine.libvirt import LibvirtDriver

console = Console()


@click.command("list")
@click.option("--uri", default="qemu:///system", show_default=True, help="Libvirt connection URI.")
def list_cmd(uri: str) -> None:
    """Show plans and the libvirt domains stamped for each one."""
    try:
        with LibvirtDriver(uri) as lv:
            owned = lv.ownership_map()
    except RuntimeError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)
    if not owned:
        console.print("No plan-owned VMs on this host.")
        return
    for plan in sorted(owned):
        console.print(f"[bold]{plan}[/bold]")
        for name in owned[plan]:
            console.print(f"  {name}")
