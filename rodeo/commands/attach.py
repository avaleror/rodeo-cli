"""rodeo attach — attach to the virsh console of a VM."""
from __future__ import annotations

import os

import click
from rich.console import Console

console = Console()

_VALID_VMS = ["harvester1", "harvester2", "harvester3", "rancher"]


@click.command("attach")
@click.argument("vm", type=click.Choice(_VALID_VMS))
def attach_cmd(vm: str) -> None:
    """Attach to the virsh serial console of a VM (Ctrl-] to detach)."""
    console.print(f"[dim]Attaching to {vm} console (Ctrl-] to detach)...[/dim]\n")
    os.execvp("virsh", ["virsh", "console", vm])
