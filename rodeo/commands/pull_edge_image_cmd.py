"""rodeo pull-edge-image — copy the EIB-built RAW from the eib VM and seed edge node disks."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console

from ..config import load_config
from ..privilege import ensure_root, is_root
from ..ssh import ssh_opts
from ._options import config_options

console = Console()

_SCP_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
]


@click.command("pull-edge-image")
@config_options
@click.option(
    "--image", "remote_image", default=None, metavar="PATH",
    help="Path to the built RAW image on the eib VM. "
         "Auto-detected from /home/eib-output/*.raw if omitted.",
)
@click.option(
    "--nodes", "node_names", default=None, metavar="NAME,...",
    help="Comma-separated edge node names to seed (default: all edge_node_names from definition).",
)
@click.option("--yes", is_flag=True, help="Overwrite existing disks without asking.")
def pull_edge_image_cmd(
    config_path: str,
    config_dir: str | None,
    params: tuple[str, ...],
    paramfile: str | None,
    remote_image: str | None,
    node_names: str | None,
    yes: bool,
) -> None:
    """Copy the EIB-built RAW image from the eib VM and create thin-clone disks for edge nodes.

    After participants build the Elemental OS image on the eib VM, run this command
    from the KVM host to pull the image and seed edge1/edge2/edge3 boot disks.
    Then start the edge nodes with 'rodeo start edge1 edge2 edge3'.
    """
    if not is_root():
        ensure_root(sys.argv[1:])

    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")

    cfg = load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)

    vms = cfg.get("vms", {})
    if "eib" not in vms:
        console.print("[red]✗  No eib VM in this lab config.[/red]")
        raise SystemExit(1)

    eib_ip = vms["eib"]["ip"]
    ssh_key = cfg.get("ssh", {}).get("identity_file")
    if not ssh_key:
        ssh_key = "/root/.ssh/id_ed25519" if os.geteuid() == 0 else str(Path.home() / ".ssh" / "id_ed25519")

    image_dir = Path(cfg.get("storage", {}).get("image_dir", "/var/lib/libvirt/images"))

    # Determine which edge nodes to seed
    edge_names: list[str]
    if node_names:
        edge_names = [n.strip() for n in node_names.split(",")]
    else:
        edge_names = cfg.get("edge_node_names") or [n for n in vms if n.startswith("edge")]
    if not edge_names:
        console.print("[red]✗  No edge node names found. Use --nodes or check definition.[/red]")
        raise SystemExit(1)

    # Auto-detect the RAW image on the eib VM if not specified
    if not remote_image:
        console.print(f"  Searching for RAW images in /home/eib-output on {eib_ip}...")
        r = subprocess.run(
            ["ssh", "-i", ssh_key, *ssh_opts(), f"root@{eib_ip}",
             "find /home/eib-output -maxdepth 2 -name '*.raw' | head -1"],
            capture_output=True, text=True, timeout=20,
        )
        remote_image = r.stdout.strip()
        if not remote_image:
            console.print(
                "[red]✗  No .raw file found in /home/eib-output on the eib VM.[/red]\n"
                "   Complete the EIB image build exercise first, then re-run this command.\n"
                "   Or specify --image /home/eib-output/your-image.raw"
            )
            raise SystemExit(1)
        console.print(f"  Found: {remote_image}")

    remote_basename = Path(remote_image).stem
    base_qcow = image_dir / f"{remote_basename}-base.qcow2"

    # Check for existing clone disks
    existing = [image_dir / f"{n}-vda.qcow2" for n in edge_names if (image_dir / f"{n}-vda.qcow2").exists()]
    if existing and not yes:
        console.print(f"\n[yellow]These edge disks already exist and will be replaced:[/yellow]")
        for p in existing:
            console.print(f"  {p}")
        click.confirm("\nOverwrite?", abort=True)

    # Transfer the RAW image from the eib VM
    console.print(f"\n[dim]Copying {remote_image} from eib VM — this may take several minutes...[/dim]")
    tmp_raw = image_dir / f"{remote_basename}.raw.tmp"
    try:
        subprocess.run(
            ["scp", "-i", ssh_key, *_SCP_OPTS, f"root@{eib_ip}:{remote_image}", str(tmp_raw)],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]✗  scp failed: {exc}[/red]")
        tmp_raw.unlink(missing_ok=True)
        raise SystemExit(1)

    # Convert RAW to qcow2 base image
    console.print(f"  Converting to qcow2 base: {base_qcow.name}...")
    base_qcow.unlink(missing_ok=True)
    try:
        subprocess.run(
            ["qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(tmp_raw), str(base_qcow)],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]✗  qemu-img convert failed: {exc}[/red]")
        raise SystemExit(1)
    finally:
        tmp_raw.unlink(missing_ok=True)

    # Create thin clones for each edge node
    for name in edge_names:
        clone = image_dir / f"{name}-vda.qcow2"
        clone.unlink(missing_ok=True)
        console.print(f"  Creating thin clone: {clone.name}")
        try:
            subprocess.run(
                ["qemu-img", "create", "-f", "qcow2",
                 "-b", str(base_qcow), "-F", "qcow2", str(clone)],
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            console.print(f"[red]✗  thin-clone failed for {name}: {exc}[/red]")
            raise SystemExit(1)

    console.print(
        f"\n[bold green]✓  Edge disks ready.[/bold green]\n"
        f"   Base image : {base_qcow}\n"
        f"   Node disks : {', '.join(f'{n}-vda.qcow2' for n in edge_names)}\n"
        f"\n   Start edge nodes: rodeo start {' '.join(edge_names)}\n"
    )
