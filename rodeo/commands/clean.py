"""rodeo clean — destroy VMs, disks, and reset deploy state."""
from __future__ import annotations

import glob
import subprocess
from pathlib import Path

import click
from rich.console import Console

from ..config import load_config
from ..profiles import get_profile
from ..state import reset_from
from ._options import config_options

console = Console()


def _virsh(*args: str) -> None:
    subprocess.run(["virsh", *args], check=False, capture_output=True)


@click.command("clean")
@config_options
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def clean_cmd(
    config_path: str, params: tuple[str, ...], paramfile: str | None, yes: bool
) -> None:
    """Destroy all rodeo VMs, disks, ISOs, the libvirt network, and reset phase state."""
    cfg = load_config(config_path, params=params, paramfile=paramfile)
    image_dir = Path(cfg["storage"]["image_dir"])
    vm_names = list(cfg.get("vms", {}).keys())

    if not yes:
        console.print("\n[bold red]This will destroy all VMs, disks, and ISOs.[/bold red]")
        click.confirm("Continue?", abort=True)

    console.print()

    # Stop + undefine VMs and tear down the default network.
    # Prefer libvirt-python; fall back to virsh if it is not installed.
    try:
        from ..engine.libvirt import LibvirtDriver

        with LibvirtDriver(cfg["libvirt"]["uri"]) as lv:
            for vm in vm_names:
                console.print(f"  [dim]destroy[/dim]  {vm}")
                lv.destroy(vm)
                lv.undefine(vm)
            # The 'default' network is shared host infrastructure — only tear
            # it down when no other VMs remain that might be using it.
            others = sorted(set(lv.list_all_domain_names()) - set(vm_names))
            if others:
                console.print(
                    f"  [yellow]keep[/yellow]     libvirt default network "
                    f"[dim](other VMs exist: {', '.join(others[:5])})[/dim]"
                )
            else:
                console.print("  [dim]destroy[/dim]  libvirt default network")
                lv.net_destroy("default")
                lv.net_undefine("default")
    except RuntimeError as exc:
        # libvirt-python missing or connect refused — virsh does the same job.
        # Anything else (e.g. libvirtError mid-operation) should surface, not hide.
        console.print(f"[yellow]⚠  {exc} — falling back to virsh[/yellow]")

        for vm in vm_names:
            console.print(f"  [dim]destroy[/dim]  {vm} (virsh)")
            _virsh("destroy", vm)
            _virsh("undefine", "--nvram", vm)

        # Check for other domains before touching the shared default network (parity with libvirt path)
        try:
            res = subprocess.run(
                ["virsh", "list", "--all", "--name"],
                capture_output=True, text=True, check=False
            )
            all_names = [n.strip() for n in res.stdout.splitlines() if n.strip()]
            others = sorted(set(all_names) - set(vm_names))
            if others:
                console.print(
                    f"  [yellow]keep[/yellow]     libvirt default network "
                    f"[dim](other VMs exist: {', '.join(others[:5])})[/dim]"
                )
            else:
                console.print("  [dim]destroy[/dim]  libvirt default network (virsh)")
                _virsh("net-destroy", "default")
                _virsh("net-undefine", "default")
        except Exception:
            # If virsh list fails, be conservative and leave the network.
            console.print("  [yellow]keep[/yellow]     libvirt default network (virsh list failed)")
            pass

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

    # Reset state from kvm_host onwards (use profile phase list for profile-aware plans)
    profile = get_profile(cfg.get("type", "suse-virt"))
    reset_from("kvm_host", cfg.get("name", "default"), profile.phases)

    console.print("\n[bold green]✓  Clean complete.[/bold green] Run [bold]rodeo deploy[/bold] to start fresh.\n")
