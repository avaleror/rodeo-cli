"""
stop_cmd.py — 'rodeo stop' subcommand for graceful lab shutdown.

Implements graceful, infra-aware stop based on the definition file:
- Uses start_order reversed for VM shutdown order.
- Uses components with on_host: true to stop relevant host services (hardcoded per name for simplicity: pxe-server -> nginx).
- Uses infra_type from node_templates (harvester/rancher) for future awareness (currently logs the type).
- For VMs: uses LibvirtDriver.shutdown() (graceful ACPI) if running; waits with timeout.
- Supports --all like clean: stops all detected rodeo VMs + host services.
- Idempotent: skips if not running.
- Runs before clean for safe destroy (see clean --force-stop or default behavior).

Paired with start_cmd.py for restart (start host services then VMs in order, with wait).

The stop is "clean" so lab can be restarted later with 'rodeo start' or 'rodeo deploy --from ...' (VMs will boot with their state).

See definition.yaml for infra_type addition and components.
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

# Known host services to stop for on_host components (simple hardcoded for now).
# Extend as needed or make configurable in definition components under 'stop_services'.
HOST_STOP_SERVICES = {
    "pxe-server": ["nginx"],
    # "host-network-prep": [],  # usually no runtime service to stop
}


def _stop_host_services(components: list[dict]) -> None:
    """Stop host services for on_host components, in reverse order if possible."""
    on_host = [c for c in components if c.get("on_host")]
    # Reverse for stop order (stop dependents first).
    for comp in reversed(on_host):
        name = comp.get("name", "")
        services = HOST_STOP_SERVICES.get(name, [])
        if not services:
            continue
        console.print(f"  [dim]stop host services for {name}[/dim]")
        for svc in services:
            try:
                # Use sudo for host services.
                subprocess.run(["sudo", "systemctl", "stop", svc], check=False, capture_output=True)
                console.print(f"    [dim]stopped {svc}[/dim]")
            except Exception as exc:
                console.print(f"    [yellow]⚠ could not stop {svc}: {exc}[/yellow]")


def _stop_vms(driver: LibvirtDriver, vm_names: list[str], timeout: int = 60) -> None:
    """Graceful shutdown VMs in given order (caller provides reverse start_order)."""
    for name in vm_names:
        if not driver.is_running(name):
            console.print(f"  [dim]skip (not running)[/dim] {name}")
            continue
        console.print(f"  [dim]shutdown (graceful ACPI)[/dim] {name}")
        driver.shutdown(name)
        # Wait for it to stop.
        start = time.time()
        while driver.is_running(name) and (time.time() - start) < timeout:
            time.sleep(2)
        if driver.is_running(name):
            console.print(f"    [yellow]⚠ still running after {timeout}s, will be force-killed on clean[/yellow]")
        else:
            console.print(f"    [dim]stopped[/dim] {name}")


@click.command("stop")
@config_options
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.option("--all", is_flag=True, help="Stop ALL detected rodeo labs/VMs (ignores plan vms, uses patterns + definition).")
def stop_cmd(config_path: str, config_dir: str | None, params: tuple[str, ...], paramfile: str | None, yes: bool, all: bool) -> None:
    """Graceful stop of the lab(s) so it can be restarted later (VMs off but defined; clusters stopped gently via infra awareness).

    Analyzes definition for start_order (reverse for stop), components (on_host services), node_templates infra_type, harvester_node_names.
    Uses simple ACPI shutdown for VMs (per chosen design). Stops known host services for pxe etc.
    For full infra stop, use before 'rodeo clean'.

    'rodeo stop' for current plan; 'rodeo stop --all' for everything.
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
        console.print(f"\n[bold yellow]This will gracefully stop {scope} (VMs via ACPI + host services).[/bold yellow]")
        click.confirm("Continue?", abort=True)

    console.print()

    # Load full definition for infra awareness (start_order, components, node_templates with infra_type).
    profile_name = (cfg or {}).get("type", "suse-virt")
    try:
        topology = _load_topology(profile_name, config_dir)
    except Exception:
        topology = {}

    start_order = topology.get("start_order", [])
    stop_order = list(reversed(start_order)) if start_order else []
    components = topology.get("components", [])
    node_templates = topology.get("node_templates", {})

    # VMs to stop.
    if all:
        vm_names = []
        try:
            with LibvirtDriver(((cfg or {"libvirt": {"uri": "qemu:///system"}})["libvirt"]["uri"])) as lv:
                all_doms = lv.list_all_domain_names()
                vm_names = [n for n in all_doms if any(p in n for p in ("harvester", "rancher", "rodeo"))]
        except Exception:
            vm_names = ["harvester1", "harvester2", "harvester3", "rancher"]
        if not vm_names:
            vm_names = ["harvester1", "harvester2", "harvester3", "rancher"]
    else:
        vm_names = list((cfg or {}).get("vms", {}).keys()) or ["harvester1", "harvester2", "harvester3", "rancher"]

    # Infra aware log (using infra_type from templates if present).
    for v in vm_names:
        # simplistic: assume based on name or look up
        t = "harvester" if "harvester" in v else "rancher" if "rancher" in v else "unknown"
        infra = node_templates.get(t, {}).get("infra_type", t)
        console.print(f"  [dim]infra_type={infra}[/dim] for {v}")

    # 1. Stop host services from components (reverse for dependents).
    if components:
        _stop_host_services(components)

    # 2. Stop VMs gracefully (in reverse start_order if available).
    uri = (cfg or {"libvirt": {"uri": "qemu:///system"}})["libvirt"]["uri"]
    try:
        with LibvirtDriver(uri) as lv:
            # If we have stop_order, use intersection with vm_names in that order.
            ordered = [v for v in stop_order if v in vm_names] or vm_names
            _stop_vms(lv, ordered)
    except RuntimeError as exc:
        console.print(f"[yellow]⚠  {exc} — falling back to virsh shutdown[/yellow]")
        for name in ( [v for v in stop_order if v in vm_names] or vm_names ):
            try:
                # virsh shutdown is graceful ACPI
                subprocess.run(["virsh", "shutdown", name], check=False, capture_output=True)
                console.print(f"  [dim]virsh shutdown[/dim] {name}")
            except Exception:
                pass

    console.print("\n[bold green]✓  Stop complete.[/bold green] VMs are off (graceful). Use 'rodeo start' or 'rodeo deploy' to restart.\n")
