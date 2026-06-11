"""rodeo attach — attach to the virsh console of a VM."""
from __future__ import annotations

import os

import click
from rich.console import Console

console = Console()


@click.command("attach")
@click.argument("vm")
@click.option("--config", "config_path", default="rodeo-plan.yaml", show_default=True)
def attach_cmd(vm: str, config_path: str) -> None:
    """Attach to the virsh serial console of a VM (Ctrl-] to detach)."""
    from ..config import load_config

    vms = load_config(config_path).get("vms", {})
    if vm not in vms:
        console.print(f"[red]✗  Unknown VM '{vm}'. Known: {', '.join(vms)}[/red]")
        raise SystemExit(1)

    console.print(f"[dim]Attaching to {vm} console (Ctrl-] to detach)...[/dim]\n")
    os.execvp("virsh", ["virsh", "console", vm])
