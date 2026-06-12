"""rodeo attach — attach to the virsh console of a VM (respects `libvirt.uri` from plan)."""
from __future__ import annotations

import os

import click
from rich.console import Console

console = Console()


@click.command("attach")
@click.argument("vm")
@click.option("--config", "config_path", default="rodeo-plan.yaml", show_default=True)
def attach_cmd(vm: str, config_path: str) -> None:
    """Attach to the virsh serial console of a VM (Ctrl-] to detach; honors libvirt.uri from plan)."""
    from ..config import load_config

    cfg = load_config(config_path)
    vms = cfg.get("vms", {})
    if vm not in vms:
        console.print(f"[red]✗  Unknown VM '{vm}'. Known: {', '.join(vms)}[/red]")
        raise SystemExit(1)

    libvirt_uri = cfg.get("libvirt", {}).get("uri", "qemu:///system")
    console.print(f"[dim]Attaching to {vm} console (Ctrl-] to detach)...[/dim]\n")

    virsh_cmd = ["virsh"]
    if libvirt_uri and libvirt_uri != "qemu:///system":
        virsh_cmd += ["-c", libvirt_uri]
    virsh_cmd += ["console", vm]
    os.execvp("virsh", virsh_cmd)
