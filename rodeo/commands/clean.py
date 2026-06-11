"""rodeo clean — destroy VMs, disks, and reset deploy state."""
from __future__ import annotations

import glob
import subprocess
from pathlib import Path

import click
from rich.console import Console

from ..config import load_config
from ..state import reset_from

console = Console()


def _virsh(*args: str) -> None:
    subprocess.run(["virsh", *args], check=False, capture_output=True)


@click.command("clean")
@click.option("--config", "config_path", default="rodeo-plan.yaml", show_default=True)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def clean_cmd(config_path: str, yes: bool) -> None:
    """Destroy all rodeo VMs, disks, ISOs, the libvirt network, and reset phase state."""
    cfg = load_config(config_path)
    image_dir = Path(cfg["storage"]["image_dir"])

    if not yes:
        click.confirm(
            "\nThis will [bold red]destroy all VMs, disks, and ISOs[/bold red]. Continue?",
            abort=True,
        )

    console.print()

    # Stop + undefine VMs
    for vm in list(cfg.get("vms", {}).keys()):
        console.print(f"  [dim]destroy[/dim]  {vm}")
        _virsh("destroy", vm)
        _virsh("undefine", "--nvram", vm)

    # Tear down the default network
    console.print("  [dim]destroy[/dim]  libvirt default network")
    _virsh("net-destroy", "default")
    _virsh("net-undefine", "default")

    # Delete disk images and OVMF vars
    patterns = [
        "harvester*.qcow2", "harvester*_vars.bin",
        "rancher*.qcow2",
        "harvester-config-*.iso",
        "harvester-v*-amd64.iso",
        "Leap-*.qcow2",
    ]
    for pat in patterns:
        for f in glob.glob(str(image_dir / pat)):
            console.print(f"  [dim]delete[/dim]   {f}")
            Path(f).unlink(missing_ok=True)

    # Reset state from kvm_host onwards
    reset_from("kvm_host", cfg.get("name", "default"))

    console.print("\n[bold green]✓  Clean complete.[/bold green] Run [bold]rodeo deploy[/bold] to start fresh.\n")
