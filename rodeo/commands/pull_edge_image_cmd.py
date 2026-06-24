"""rodeo pull-edge-image — seed edge node disks from the eib VM or a local image file.

Two modes:
  Remote (default): SSH to the eib VM, find the EIB-built RAW in /home/eib-output,
  SCP it to the KVM host, convert to qcow2, and thin-clone for each edge node.

  Local (--local PATH): skip the SSH/SCP step and use an image already on the KVM host.
  Format is auto-detected from the file extension:
    .raw    → convert to qcow2 base → thin clones (same path as remote)
    .qcow2  → use as backing file directly → thin clones
    .iso    → create a blank vda.qcow2 per node + redefine the domain XML so the ISO
              is a CDROM device (sda, boot-order-1) and vda is boot-order-2.
              Boot the nodes with 'rodeo start <nodes>'; Elemental installs and powers off.
              Then run 'rodeo eject-iso' to remove the CDROM and restore disk-first boot.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
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


def _thin_clone(base: Path, clone: Path) -> None:
    clone.unlink(missing_ok=True)
    subprocess.run(
        ["qemu-img", "create", "-f", "qcow2", "-b", str(base), "-F", "qcow2", str(clone)],
        check=True,
    )


def _redefine_with_iso(domain: str, iso_path: str, blank_disk: Path, disk_gb: int, uri: str) -> None:
    """Add ISO as CDROM (sda, boot-order-1) and ensure vda exists (boot-order-2).

    Parses the inactive domain XML, inserts the CDROM device, updates the vda boot
    order, and redefines the domain. Safe to call while the domain is shut off.
    """
    if not blank_disk.exists():
        console.print(f"  Creating blank disk {blank_disk.name} ({disk_gb} GB)...")
        subprocess.run(
            ["qemu-img", "create", "-f", "qcow2", str(blank_disk), f"{disk_gb}G"],
            check=True,
        )

    r = subprocess.run(
        ["virsh", "-c", uri, "dumpxml", "--inactive", domain],
        capture_output=True, text=True, check=True,
    )

    ET.register_namespace("", "")
    root = ET.fromstring(r.stdout)
    devices = root.find("devices")
    if devices is None:
        raise RuntimeError(f"No <devices> element in domain XML for {domain}")

    # Remove any existing CDROM devices (avoid duplicates on re-run)
    for disk_el in list(devices.findall("disk")):
        if disk_el.get("device") == "cdrom":
            devices.remove(disk_el)

    # Set vda boot order to 2
    for disk_el in devices.findall("disk"):
        tgt = disk_el.find("target")
        if tgt is not None and tgt.get("dev") == "vda":
            boot_el = disk_el.find("boot")
            if boot_el is None:
                boot_el = ET.SubElement(disk_el, "boot")
            boot_el.set("order", "2")

    # Insert CDROM with boot order 1
    cdrom_el = ET.fromstring(
        f"<disk type='file' device='cdrom'>"
        f"<driver name='qemu' type='raw'/>"
        f"<source file='{iso_path}'/>"
        f"<target dev='sda' bus='sata'/>"
        f"<readonly/>"
        f"<boot order='1'/>"
        f"</disk>"
    )
    devices.append(cdrom_el)

    xml_out = ET.tostring(root, encoding="unicode")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
        f.write(xml_out)
        tmpfile = f.name
    try:
        subprocess.run(["virsh", "-c", uri, "define", tmpfile], check=True, capture_output=True)
    finally:
        os.unlink(tmpfile)


def _local_raw_or_qcow2(local_path: Path, image_dir: Path, edge_names: list[str], yes: bool) -> None:
    """Thin-clone flow for a local RAW or QCOW2 image."""
    suffix = local_path.suffix.lower()

    existing = [image_dir / f"{n}-vda.qcow2" for n in edge_names if (image_dir / f"{n}-vda.qcow2").exists()]
    if existing and not yes:
        console.print("\n[yellow]These edge disks already exist and will be replaced:[/yellow]")
        for p in existing:
            console.print(f"  {p}")
        click.confirm("\nOverwrite?", abort=True)

    base_qcow = image_dir / f"{local_path.stem}-base.qcow2"

    if suffix == ".raw":
        console.print(f"  Converting {local_path.name} → {base_qcow.name}...")
        base_qcow.unlink(missing_ok=True)
        subprocess.run(
            ["qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(local_path), str(base_qcow)],
            check=True,
        )
    else:
        base_qcow = local_path  # use qcow2 directly as backing file

    for name in edge_names:
        clone = image_dir / f"{name}-vda.qcow2"
        console.print(f"  Thin clone: {clone.name}")
        _thin_clone(base_qcow, clone)

    console.print(
        f"\n[bold green]✓  Edge disks ready.[/bold green]\n"
        f"   Base : {base_qcow}\n"
        f"   Disks: {', '.join(f'{n}-vda.qcow2' for n in edge_names)}\n"
        f"\n   Start edge nodes: rodeo start {' '.join(edge_names)}\n"
    )


def _local_iso(
    local_path: Path, image_dir: Path, edge_names: list[str], cfg: dict, yes: bool
) -> None:
    """ISO install flow: blank disk + redefine domain with ISO as CDROM."""
    uri = cfg.get("libvirt", {}).get("uri", "qemu:///system")
    disk_gb = cfg.get("resources", {}).get("edge-node", {}).get("disk_gb", 20)

    existing_disks = [image_dir / f"{n}-vda.qcow2" for n in edge_names if (image_dir / f"{n}-vda.qcow2").exists()]
    if existing_disks and not yes:
        console.print("\n[yellow]These edge disks exist and will be reset to blank:[/yellow]")
        for p in existing_disks:
            console.print(f"  {p}")
        click.confirm("\nContinue?", abort=True)

    for name in edge_names:
        blank = image_dir / f"{name}-vda.qcow2"
        blank.unlink(missing_ok=True)
        console.print(f"  {name}: blank disk + attaching ISO as CDROM...")
        try:
            _redefine_with_iso(name, str(local_path), blank, disk_gb, uri)
        except subprocess.CalledProcessError as exc:
            console.print(f"[red]✗  Failed to redefine {name}: {exc}[/red]")
            raise SystemExit(1)
        console.print(f"  [dim]{name} redefined with ISO as CDROM (boot-order-1)[/dim]")

    console.print(
        f"\n[bold green]✓  Edge nodes ready for ISO install.[/bold green]\n"
        f"   ISO  : {local_path}\n"
        f"   Nodes: {', '.join(edge_names)}\n"
        f"\n   Boot the nodes:  rodeo start {' '.join(edge_names)}\n"
        f"   Elemental installs and powers off the VM.\n"
        f"   After poweroff:  rodeo eject-iso --nodes {','.join(edge_names)}\n"
        f"   Then start again: rodeo start {' '.join(edge_names)}\n"
    )


@click.command("pull-edge-image")
@config_options
@click.option(
    "--image", "remote_image", default=None, metavar="PATH",
    help="Path to the built RAW image on the eib VM. "
         "Auto-detected from /home/eib-output/*.raw if omitted. "
         "Ignored when --local is set.",
)
@click.option(
    "--local", "local_image", default=None, metavar="PATH",
    type=click.Path(exists=True, dir_okay=False),
    help="Use a local image file instead of pulling from the eib VM. "
         "Accepts .raw, .qcow2, or .iso. Format is auto-detected from the extension.",
)
@click.option(
    "--nodes", "node_names", default=None, metavar="NAME,...",
    help="Comma-separated edge node names to seed (default: all edge nodes from definition).",
)
@click.option("--yes", is_flag=True, help="Overwrite existing disks without asking.")
def pull_edge_image_cmd(
    config_path: str,
    config_dir: str | None,
    params: tuple[str, ...],
    paramfile: str | None,
    remote_image: str | None,
    local_image: str | None,
    node_names: str | None,
    yes: bool,
) -> None:
    """Seed edge node boot disks from the eib VM or a local image file.

    \b
    Remote mode (default):
      Pulls the EIB-built RAW from the eib VM, converts to qcow2, thin-clones.

    \b
    Local mode (--local PATH):
      .raw / .qcow2  Convert and thin-clone without touching the eib VM.
      .iso           Create blank disks, attach ISO as CDROM, redefine the domain.
                     Boot with 'rodeo start', wait for Elemental to install and power off,
                     then run 'rodeo eject-iso' to restore disk-first boot.
    """
    if not is_root():
        ensure_root(sys.argv[1:])

    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")

    cfg = load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)
    vms = cfg.get("vms", {})
    image_dir = Path(cfg.get("storage", {}).get("image_dir", "/var/lib/libvirt/images"))

    # Resolve edge node list
    edge_names: list[str]
    if node_names:
        edge_names = [n.strip() for n in node_names.split(",")]
    else:
        edge_names = cfg.get("edge_node_names") or [n for n in vms if n.startswith("edge")]
    if not edge_names:
        console.print("[red]✗  No edge node names found. Use --nodes or check definition.[/red]")
        raise SystemExit(1)

    # --- Local mode ---
    if local_image:
        local_path = Path(local_image)
        suffix = local_path.suffix.lower()
        console.print(f"\n  Local image: {local_path}  (format: {suffix[1:] if suffix else 'unknown'})")
        if suffix == ".iso":
            _local_iso(local_path, image_dir, edge_names, cfg, yes)
        elif suffix in (".raw", ".qcow2", ".qcow"):
            _local_raw_or_qcow2(local_path, image_dir, edge_names, yes)
        else:
            console.print(f"[red]✗  Unsupported format '{suffix}'. Use .raw, .qcow2, or .iso.[/red]")
            raise SystemExit(1)
        return

    # --- Remote mode: pull from eib VM ---
    if "eib" not in vms:
        console.print("[red]✗  No eib VM in this lab config. Use --local to specify a local image.[/red]")
        raise SystemExit(1)

    eib_ip = vms["eib"]["ip"]
    ssh_key = cfg.get("ssh", {}).get("identity_file")
    if not ssh_key:
        ssh_key = "/root/.ssh/id_ed25519" if os.geteuid() == 0 else str(Path.home() / ".ssh" / "id_ed25519")

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
                "   Or use --local /path/to/image.raw to use a local image."
            )
            raise SystemExit(1)
        console.print(f"  Found: {remote_image}")

    remote_basename = Path(remote_image).stem
    base_qcow = image_dir / f"{remote_basename}-base.qcow2"

    existing = [image_dir / f"{n}-vda.qcow2" for n in edge_names if (image_dir / f"{n}-vda.qcow2").exists()]
    if existing and not yes:
        console.print("\n[yellow]These edge disks already exist and will be replaced:[/yellow]")
        for p in existing:
            console.print(f"  {p}")
        click.confirm("\nOverwrite?", abort=True)

    console.print(f"\n[dim]Copying {remote_image} from eib VM ({eib_ip}) — this may take several minutes...[/dim]")
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

    for name in edge_names:
        clone = image_dir / f"{name}-vda.qcow2"
        clone.unlink(missing_ok=True)
        console.print(f"  Thin clone: {clone.name}")
        _thin_clone(base_qcow, clone)

    console.print(
        f"\n[bold green]✓  Edge disks ready.[/bold green]\n"
        f"   Base : {base_qcow}\n"
        f"   Disks: {', '.join(f'{n}-vda.qcow2' for n in edge_names)}\n"
        f"\n   Start edge nodes: rodeo start {' '.join(edge_names)}\n"
    )
