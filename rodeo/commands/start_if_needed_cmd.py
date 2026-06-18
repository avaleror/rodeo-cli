"""
start_if_needed_cmd.py — 'rodeo start-if-needed' idempotent boot guard.

Designed for Instruqt challenge setup scripts and on-boot systemd units.
Starts host services and VMs only when not already running, then enforces
nftables rules (DNAT + libvirt guest_input accept-before-reject). Calls
manage_nft_rules.sh explicitly so rules are correct even when the qemu
hook never fired (e.g. VMs were already running from a resumed image).
No confirmation prompt. Exits 0 whether or not any action was taken.
"""

from __future__ import annotations

import os
import subprocess
import time

import click
from rich.console import Console

from ..config import load_config
from ..inventory import _load_topology
from ..engine.libvirt import LibvirtDriver
from .start_cmd import _start_host_services

console = Console()

_NFT_SCRIPT = "/usr/local/bin/manage_nft_rules.sh"
_DEFAULT_VMS = ["harvester1", "harvester2", "harvester3"]


@click.command("start-if-needed")
@click.option("--config-dir", "config_dir", default=None, metavar="DIR",
              type=click.Path(file_okay=False, dir_okay=True, exists=False),
              help="Lab config directory (definition.yaml + plan). Optional — defaults apply if absent.")
@click.option("--config", "config_path", default="rodeo-plan.yaml", metavar="FILE",
              type=click.Path(), help="Plan file path.")
def start_if_needed_cmd(config_dir: str | None, config_path: str) -> None:
    """Idempotent boot guard: start VMs and enforce nftables rules if not already running.

    Safe to call from Instruqt setup scripts or a systemd on-boot unit.
    Exits 0 whether the lab needed starting or was already up.

    \b
    What it does:
      1. Start on-host services (nginx/pxe) if definition declares them.
      2. Start VMs in definition order, skip any already running.
      3. Call manage_nft_rules.sh to enforce DNAT and ct-status-dnat-accept rules.
    """
    ctx = click.get_current_context()
    if config_dir is None and ctx.obj:
        config_dir = ctx.obj.get("config_dir")

    cfg = None
    try:
        cfg = load_config(config_path, config_dir=config_dir)
    except Exception:
        pass

    profile_name = (cfg or {}).get("type", "suse-virt")
    try:
        topology = _load_topology(profile_name, config_dir)
    except Exception:
        topology = {}

    start_order = topology.get("start_order", [])
    components = topology.get("components", [])
    vm_names = list((cfg or {}).get("vms", {}).keys()) or _DEFAULT_VMS
    uri = (cfg or {}).get("libvirt", {}).get("uri", "qemu:///system")

    if components:
        _start_host_services(components)

    try:
        with LibvirtDriver(uri) as lv:
            ordered = [v for v in start_order if v in vm_names] or vm_names
            for name in ordered:
                if lv.is_running(name):
                    console.print(f"  [dim]already running[/dim] {name}")
                    continue
                console.print(f"  [dim]start[/dim] {name}")
                try:
                    lv.start(name)
                    deadline = time.time() + 30
                    while not lv.is_running(name) and time.time() < deadline:
                        time.sleep(1)
                    console.print(f"    [dim]started[/dim] {name}")
                except Exception as exc:
                    console.print(f"    [yellow]⚠  {name}: {exc}[/yellow]")
    except Exception as exc:
        console.print(f"[yellow]⚠  libvirt unavailable: {exc} — skipping VM start[/yellow]")

    # Enforce nftables rules regardless of whether VMs just started or were
    # already running. The qemu hook fires on VM start events but not when
    # VMs are resumed from a saved image — explicit call here covers that gap.
    if os.path.exists(_NFT_SCRIPT):
        try:
            subprocess.run(["bash", _NFT_SCRIPT], check=False, capture_output=True)
            console.print("  [dim]nft rules enforced[/dim]")
        except Exception as exc:
            console.print(f"  [yellow]⚠  nft enforcement: {exc}[/yellow]")

    console.print("\n[bold green]✓  Lab ready.[/bold green]\n")
