"""rodeo clean — destroy VMs, disks, and reset deploy state."""
from __future__ import annotations

import glob
import subprocess
import sys
import time
from pathlib import Path

import click
from rich.console import Console

from ..engine.libvirt import LibvirtDriver, discover_rodeo_vm_names

from ..config import load_config
from ..privilege import ensure_root, is_root
from ..profiles import get_profile
from ..state import reset_from
from ._options import config_options

console = Console()


def _virsh(*args: str, uri: str | None = None) -> None:
    cmd = ["virsh"]
    if uri:
        cmd.extend(["-c", uri])
    cmd.extend(args)
    subprocess.run(cmd, check=False, capture_output=True)


@click.command("clean")
@config_options
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.option("--all", is_flag=True, help="Host reset mode: destroy ALL rodeo-related VMs (by name pattern), unconditionally clean the default libvirt network, delete all rodeo disk/ISO artifacts, clear ALL plan states, and (optionally with --secrets) remove global secrets. Leaves installed packages and the rodeo binary/link intact. Ideal for repurposing the host or starting completely fresh testing.")
@click.option("--force-network", is_flag=True, help="Force destroy the libvirt 'default' network even if other non-rodeo VMs exist.")
@click.option("--secrets", is_flag=True, help="Also remove ~/.rodeo/secrets.yaml (global passwords for the plan). Use with --all or --yes for host reset.")
@click.option("--hard", is_flag=True, help="Hard destroy (skip graceful stop first). Default is to run stop logic for VMs if running, for clean stopped state before destroy/undefine.")
@click.option("--refresh", is_flag=True, help="After cleaning, update rodeo-cli to the latest upstream code (same robust path as 'rodeo self-update'). Off by default so clean never changes the CLI version out from under you — important for pinned/Instruqt hosts.")
def clean_cmd(
    config_path: str, config_dir: str | None, params: tuple[str, ...], paramfile: str | None, yes: bool, all: bool, force_network: bool, secrets: bool, hard: bool, refresh: bool
) -> None:
    """Destroy rodeo VMs, disks, ISOs, the libvirt network (per-plan or --all host reset), reset phase state, and optionally secrets.

    By default operates on the plan from config/plan file (specific VMs + plan state).
    Use --all for full host cleanup without needing a specific plan/config (matches rodeo-like VM names).

    Graceful stop integration: unless --hard, will first run stop logic (VMs via shutdown if running) for clean state before destroy. See stop_cmd.py and user docs.
    """
    if not is_root():
        ensure_root(sys.argv[1:])

    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")

    # For --all we can operate without a full cfg (no vms from plan), but load if provided for name etc.
    cfg = None
    if not all or config_path or config_dir or params or paramfile:
        try:
            cfg = load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)
        except Exception:
            if not all:
                raise
            cfg = {"name": "default", "storage": {"image_dir": "/var/lib/libvirt/images"} }

    image_dir = Path( (cfg or {}).get("storage", {}).get("image_dir", "/var/lib/libvirt/images") )
    uri0 = (cfg or {"libvirt": {"uri": "qemu:///system"}})["libvirt"]["uri"]
    if all:
        # Discover rodeo VMs actually on the host (harvester*, rancher*, edge*,
        # eib, rodeo*) across any plan — never assume a fixed node set.
        vm_names = discover_rodeo_vm_names(uri0)
    else:
        vm_names = list( (cfg or {}).get("vms", {}).keys() ) or discover_rodeo_vm_names(uri0)

    # Confirmation: tailor message for --all vs normal per-plan clean.
    if not yes:
        if all:
            msg = "This will DESTROY ALL rodeo VMs (harvester*, rancher*, etc.), the default libvirt network, all disk/ISO artifacts, ALL plan state files"
            if secrets:
                msg += ", and the global secrets.yaml"
            msg += ". This fully resets the host for fresh testing or repurposing (packages and rodeo binary are left alone)."
            console.print(f"\n[bold red]{msg}[/bold red]")
        else:
            console.print("\n[bold red]This will destroy all VMs, disks, and ISOs for the current plan.[/bold red]")
        click.confirm("Continue?", abort=True)

    console.print()

    # If not --hard, do graceful stop first (VMs via ACPI shutdown) so clean happens on stopped infra.
    # This ensures 'rodeo clean' (even without explicit 'rodeo stop') leaves clean state; --hard for immediate force.
    if not hard:
        try:
            uri = (cfg or {"libvirt": {"uri": "qemu:///system"}})["libvirt"]["uri"]
            with LibvirtDriver(uri) as lv:
                for vm in vm_names:
                    if lv.is_running(vm):
                        console.print(f"  [dim]graceful stop (before destroy)[/dim] {vm}")
                        lv.shutdown(vm)
                        # short wait
                        for _ in range(15):
                            if not lv.is_running(vm):
                                break
                            time.sleep(2)
        except Exception as exc:
            console.print(f"[yellow]⚠ graceful stop before clean failed ({exc}), will hard destroy[/yellow]")

    # Stop + undefine VMs and tear down the default network.
    # Prefer libvirt-python; fall back to virsh if it is not installed.
    # For --all or --force-network we destroy the network unconditionally (after VM cleanup).
    do_force_net = all or force_network

    uri = (cfg or {"libvirt": {"uri": "qemu:///system"}})["libvirt"]["uri"]
    try:
        with LibvirtDriver(uri) as lv:
            for vm in vm_names:
                console.print(f"  [dim]destroy[/dim]  {vm}")
                lv.destroy(vm)
                lv.undefine(vm)
            others = sorted(set(lv.list_all_domain_names()) - set(vm_names))
            if others and not do_force_net:
                console.print(
                    f"  [yellow]keep[/yellow]     libvirt default network "
                    f"[dim](other VMs exist: {', '.join(others[:5])})[/dim]"
                )
            else:
                if others and do_force_net:
                    console.print("  [yellow]force[/yellow]    libvirt default network ( --force-network / --all )")
                console.print("  [dim]destroy[/dim]  libvirt default network")
                lv.net_destroy("default")
                lv.net_undefine("default")
    except RuntimeError as exc:
        console.print(f"[yellow]⚠  {exc} — falling back to virsh[/yellow]")

        for vm in vm_names:
            console.print(f"  [dim]destroy[/dim]  {vm} (virsh)")
            _virsh("destroy", vm, uri=uri)
            _virsh("undefine", "--nvram", vm, uri=uri)

        try:
            res = subprocess.run(
                ["virsh", "-c", uri, "list", "--all", "--name"],
                capture_output=True, text=True, check=False
            )
            all_names = [n.strip() for n in res.stdout.splitlines() if n.strip()]
            others = sorted(set(all_names) - set(vm_names))
            if others and not do_force_net:
                console.print(
                    f"  [yellow]keep[/yellow]     libvirt default network "
                    f"[dim](other VMs exist: {', '.join(others[:5])})[/dim]"
                )
            else:
                if others and do_force_net:
                    console.print("  [yellow]force[/yellow]    libvirt default network (virsh, --force-network / --all)")
                console.print("  [dim]destroy[/dim]  libvirt default network (virsh)")
                _virsh("net-destroy", "default", uri=uri)
                _virsh("net-undefine", "default", uri=uri)
        except Exception:
            console.print("  [yellow]keep[/yellow]     libvirt default network (virsh list failed)")
            pass

    # Delete disk images, OVMF var stores, seed ISOs and base images across every
    # profile (suse-virt / rancher / suse-edge). Patterns are scoped to rodeo node
    # name prefixes (harvester/rancher/eib/edge) so we never touch a non-rodeo VM's
    # disk that happens to share the pool. Includes the interrupted-transfer temp
    # files (.building from qemu-img convert, .downloading from curl) a failed run
    # leaves behind — otherwise a stale partial can poison the next deploy.
    patterns = [
        # VM disks + interrupted qemu-img convert temp files
        "harvester*-vda.qcow2", "rancher-vda.qcow2", "eib-vda.qcow2", "edge*-vda.qcow2",
        "*-vda.qcow2.building",
        # OVMF UEFI variable stores (real name is <node>-ovmf-vars.fd; keep the
        # legacy *_vars.bin so older labs still get cleaned)
        "harvester*-ovmf-vars.fd", "rancher-ovmf-vars.fd", "eib-ovmf-vars.fd",
        "edge*-ovmf-vars.fd", "harvester*_vars.bin",
        # config / cloud-init seed ISOs (harvester nodes + rancher/eib cloud-init)
        "harvester-config-*.iso", "rancher-cloud-init.iso", "eib-cloud-init.iso",
        # base images + interrupted curl downloads
        "harvester-v*-amd64.iso", "Leap-*.qcow2", "Leap-*.qcow2.downloading",
        "SL-Micro*.iso", "SL-Micro*.raw",
    ]
    for pat in patterns:
        for f in glob.glob(str(image_dir / pat)):
            console.print(f"  [dim]delete[/dim]   {f}")
            Path(f).unlink(missing_ok=True)

    # Reset state. For --all we nuke all plan state files (for repurposing / fresh start).
    # For normal per-plan we reset from kvm_host for the specific plan (as before).
    if all:
        state_dir = Path.home() / ".rodeo" / "state"
        if state_dir.exists():
            for f in state_dir.glob("*.yaml"):
                console.print(f"  [dim]delete[/dim]   state {f}")
                f.unlink(missing_ok=True)
    else:
        profile = get_profile( (cfg or {}).get("type", "suse-virt") )
        reset_from("kvm_host", (cfg or {}).get("name", "default"), profile.phases)

    # Secrets (global ~/.rodeo/secrets.yaml ). Only if --secrets (or --all + --secrets).
    if secrets:
        secrets_path = Path.home() / ".rodeo" / "secrets.yaml"
        if secrets_path.exists():
            console.print(f"  [dim]delete[/dim]   {secrets_path}")
            secrets_path.unlink(missing_ok=True)

    console.print("\n[bold green]✓  Clean complete.[/bold green]")
    if all:
        console.print("Host reset done. Packages and rodeo binary remain. You can now start fresh or repurpose the node.\n")
    else:
        console.print("Run [bold]rodeo deploy[/bold] to start fresh.\n")

    # CLI refresh is OPT-IN only. Cleaning must never silently change the rodeo
    # version — on a pinned or Instruqt host that makes "which version am I
    # running" non-deterministic, and the old always-on fast-forward pull both
    # no-op'd on a stale remote and misreported the version. When asked, route
    # through the robust self-update path (fetch + hard-align + verify).
    if refresh:
        from .self_update_cmd import run_self_update
        console.print("Refreshing rodeo-cli...")
        try:
            run_self_update()
        except SystemExit as exc:
            # The clean itself already succeeded; surface the refresh failure
            # without pretending the whole command failed.
            console.print(f"[yellow]⚠  clean succeeded, but --refresh failed (exit {exc.code}). "
                          "Run 'rodeo self-update' to see why.[/yellow]")
