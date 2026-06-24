"""rodeo eject-iso — remove the CDROM from edge nodes after an ISO-based Elemental install.

After 'rodeo pull-edge-image --local image.iso' + 'rodeo start <nodes>', Elemental
boots from the ISO, installs to vda, and powers off. Run this command once the VMs
are shut off to:
  1. Remove the CDROM disk device from the libvirt domain XML.
  2. Restore vda boot order to 1.
  3. Redefine the domain so the next start boots from disk.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import click
from rich.console import Console

from ..config import load_config
from ..privilege import ensure_root, is_root
from ._options import config_options

console = Console()


def _eject_iso(domain: str, uri: str) -> None:
    """Remove CDROM devices from the domain and restore vda to boot-order-1."""
    r = subprocess.run(
        ["virsh", "-c", uri, "dumpxml", "--inactive", domain],
        capture_output=True, text=True, check=True,
    )

    root = ET.fromstring(r.stdout)
    devices = root.find("devices")
    if devices is None:
        raise RuntimeError(f"No <devices> in domain XML for {domain}")

    removed = 0
    for disk_el in list(devices.findall("disk")):
        if disk_el.get("device") == "cdrom":
            devices.remove(disk_el)
            removed += 1

    # Restore vda to boot-order-1
    for disk_el in devices.findall("disk"):
        tgt = disk_el.find("target")
        if tgt is not None and tgt.get("dev") == "vda":
            boot_el = disk_el.find("boot")
            if boot_el is not None:
                boot_el.set("order", "1")

    xml_out = ET.tostring(root, encoding="unicode")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
        f.write(xml_out)
        tmpfile = f.name
    try:
        subprocess.run(["virsh", "-c", uri, "define", tmpfile], check=True, capture_output=True)
    finally:
        os.unlink(tmpfile)

    if removed == 0:
        console.print(f"  [yellow]⚠  {domain}: no CDROM found — already ejected?[/yellow]")
    else:
        console.print(f"  [dim]✓  {domain}: CDROM removed, vda is now boot-order-1[/dim]")


@click.command("eject-iso")
@config_options
@click.option(
    "--nodes", "node_names", default=None, metavar="NAME,...",
    help="Comma-separated edge node names (default: all edge nodes from definition).",
)
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def eject_iso_cmd(
    config_path: str,
    config_dir: str | None,
    params: tuple[str, ...],
    paramfile: str | None,
    node_names: str | None,
    yes: bool,
) -> None:
    """Remove the ISO CDROM from edge nodes and restore disk-first boot after Elemental install.

    Run this after 'rodeo start <nodes>' boots and Elemental powers off the VMs.
    Then 'rodeo start <nodes>' again to boot from the installed disk.
    """
    if not is_root():
        ensure_root(sys.argv[1:])

    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")

    cfg = load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)
    vms = cfg.get("vms", {})
    uri = cfg.get("libvirt", {}).get("uri", "qemu:///system")

    edge_names: list[str]
    if node_names:
        edge_names = [n.strip() for n in node_names.split(",")]
    else:
        edge_names = cfg.get("edge_node_names") or [n for n in vms if n.startswith("edge")]
    if not edge_names:
        console.print("[red]✗  No edge node names found. Use --nodes.[/red]")
        raise SystemExit(1)

    # Warn if any VM is still running
    for name in edge_names:
        r = subprocess.run(
            ["virsh", "-c", uri, "domstate", name],
            capture_output=True, text=True,
        )
        state = r.stdout.strip().lower()
        if state == "running":
            console.print(
                f"[red]✗  {name} is still running.[/red]\n"
                "   Wait for Elemental to finish installing (it powers off the VM),\n"
                "   then re-run this command."
            )
            raise SystemExit(1)

    if not yes:
        console.print(
            f"\n[bold yellow]Remove ISO CDROM from: {', '.join(edge_names)}[/bold yellow]"
        )
        click.confirm("Continue?", abort=True)

    console.print()
    for name in edge_names:
        try:
            _eject_iso(name, uri)
        except subprocess.CalledProcessError as exc:
            console.print(f"[red]✗  {name}: {exc}[/red]")
            raise SystemExit(1)

    console.print(
        f"\n[bold green]✓  ISO ejected.[/bold green]  "
        f"Boot from disk: rodeo start {' '.join(edge_names)}\n"
    )
