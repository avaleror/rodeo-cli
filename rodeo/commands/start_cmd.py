"""
start_cmd.py — 'rodeo start' subcommand, symmetric to stop.

Starts host services (from definition components on_host) then VMs in start_order.
Uses driver.start(), with basic wait using is_running loop (per design choice).

Infra aware via same definition analysis as stop (infra_type logged, components for host services).
"""

import subprocess
import time
from pathlib import Path

import click
from rich.console import Console

from ..config import load_config
from ..inventory import _load_topology
from ._options import config_options
from ..engine.libvirt import LibvirtDriver

console = Console()

# Mirror from stop.
HOST_START_SERVICES = {
    "pxe-server": ["nginx"],
}


def _start_host_services(components: list[dict]) -> None:
    on_host = [c for c in components if c.get("on_host")]
    for comp in on_host:  # forward order for start
        name = comp.get("name", "")
        services = HOST_START_SERVICES.get(name, [])
        if not services:
            continue
        console.print(f"  [dim]start host services for {name}[/dim]")
        for svc in services:
            try:
                subprocess.run(["sudo", "systemctl", "start", svc], check=False, capture_output=True)
                console.print(f"    [dim]started {svc}[/dim]")
            except Exception as exc:
                console.print(f"    [yellow]⚠ could not start {svc}: {exc}[/yellow]")


@click.command("start")
@config_options
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.option("--all", is_flag=True, help="Start ALL detected rodeo VMs/labs.")
def start_cmd(config_path: str, config_dir: str | None, params: tuple[str, ...], paramfile: str | None, yes: bool, all: bool) -> None:
    """Start the lab(s) after a previous 'rodeo stop' (or power off). Starts in order from definition.

    For full bring-up after stop, you may still need 'rodeo deploy --from cluster' or similar
    to re-establish cluster health, but VMs will be running.
    """
    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")

    cfg = None
    if not all or config_path or config_dir or params or paramfile:
        try:
            cfg = load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)
        except Exception:
            if not all:
                raise
            cfg = {"type": "suse-virt", "name": "default"}

    if not yes:
        scope = "ALL rodeo labs" if all else f"plan '{(cfg or {}).get('name', 'default')}'"
        console.print(f"\n[bold yellow]This will start {scope} (host services + VMs).[/bold yellow]")
        click.confirm("Continue?", abort=True)

    console.print()

    profile_name = (cfg or {}).get("type", "suse-virt")
    try:
        topology = _load_topology(profile_name, config_dir)
    except Exception:
        topology = {}

    start_order = topology.get("start_order", [])
    components = topology.get("components", [])

    if all:
        vm_names = ["harvester1", "harvester2", "harvester3", "rancher"]  # fallback
    else:
        vm_names = list((cfg or {}).get("vms", {}).keys()) or ["harvester1", "harvester2", "harvester3", "rancher"]

    # Start host services first (forward).
    if components:
        _start_host_services(components)

    # Start VMs in order.
    uri = (cfg or {"libvirt": {"uri": "qemu:///system"}})["libvirt"]["uri"]
    try:
        with LibvirtDriver(uri) as lv:
            ordered = [v for v in start_order if v in vm_names] or vm_names
            for name in ordered:
                if lv.is_running(name):
                    console.print(f"  [dim]skip (already running)[/dim] {name}")
                    continue
                console.print(f"  [dim]start[/dim] {name}")
                lv.start(name)
                # Basic wait for ready (per user request).
                start_t = time.time()
                while not lv.is_running(name) and time.time() - start_t < 30:
                    time.sleep(1)
                console.print(f"    [dim]started[/dim] {name}")
    except RuntimeError as exc:
        console.print(f"[yellow]⚠  {exc} — falling back to virsh[/yellow]")
        for name in ( [v for v in start_order if v in vm_names] or vm_names ):
            subprocess.run(["virsh", "start", name], check=False, capture_output=True)
            console.print(f"  [dim]virsh start[/dim] {name}")

    console.print("\n[bold green]✓  Start complete.[/bold green] Use 'rodeo status' or 'rodeo deploy --from cluster' to fully restore.\n")
