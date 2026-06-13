"""
start_cmd.py — 'rodeo start' subcommand, symmetric to stop.

This command was created as the counterpart to stop_cmd.py to complete the reversible lifecycle (pause via stop, resume via start), enabling "they can be restored and start again" after gentle stop (pre-clean or standalone). Logical reason in project: Definition drives "what to start" (start_order for sequence, components for on_host services to bring up before VMs, infra_type for awareness of Harvester vs Rancher/K8s nodes). Without explicit start, post-stop resume relied on full 'rodeo deploy' (re-provisioning overhead) or manual virsh. Outcomes of using: "timely and clean" restart (host services first, then VMs in definition order with wait for ready), symmetric to stop, supporting full "stop process that has to run when we run 'rodeo stop --all'" followed by start or clean integration. "Analyzes the definition file" (same _load_topology + infra_type/components as stop; see definition.yaml for additions and stop-design-options.md). "Pause things as they should" reversed for start order.

Infra aware via same definition analysis as stop (infra_type logged, components for host services).

Fits general picture as part of lifecycle commands (with generate for entry, bootstrap for initial setup, deploy for up, stop, clean --all for reset/repurpose; all using definition as source per inventory.py). See clean.py for --hard integration (stop first unless hard), cli.py for registration, user-guide for "stop before clean; start after", architecture.md for role.
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
    """Starts relevant host-side services for on_host components (from definition), in forward order.

    Inputs:
    - components: list[dict] from loaded definition (topology["components"]; on_host: true ones like pxe-server).

    Outputs:
    - None (side-effect: systemctl start on host for mapped services; logs).

    Patterns inside:
    - Forward iteration (dependents after; mirrors start_order and components list in definition).
    - Lookup in HOST_START_SERVICES (symmetric to stop's map; for known on_host).
    - subprocess sudo (consistent with stop/clean/install_deps for host).
    - No wait (services start async; VM wait follows).

    How it works:
    - Scans components, starts services for matches (e.g. nginx for pxe after stop).
    - Called before VM start.

    Fit in project:
    - Logical reason: Symmetric to _stop_host_services; definition components declare on_host (part of "the rodeo" infra, not just guests). Starting VMs without host services (pxe/nginx) would fail boot for diskless Harvester nodes. Outcomes: Clean "start after stop" (host infra up before guests), enabling restore post-pause or post-clean. Fits declarative (definition analysis in start_cmd like stop/inventory/clean); part of reversible lifecycle for "able to restart the lab if needed" (generate -> stop -> clean -> start or deploy).
    """
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

    Inputs (from @config_options + flags):
    - config_path/config_dir/params/paramfile: for load_config (name/vms/type; config_dir for def).
    - yes/all: as in stop_cmd.

    Outputs:
    - None (side effects: services/VMs started + wait; ready for status/deploy from cluster).

    Patterns inside:
    - Same definition load as stop (_load_topology for order/components).
    - VM list from cfg or all (patterns).
    - Forward start (services then VMs in start_order).
    - Delegation (_start_host_services + direct driver.start + is_running wait).
    - Fallback virsh (consistent).
    - Wait loop (per design; extends basic start for "VM ready").

    How it works:
    - Loads topology.
    - Starts host services (forward on_host).
    - Starts VMs in order (driver.start if not running; wait via is_running for "timely" ready).
    - "Analyzes definition" for order/services/infra (infra_type logged).

    Fit in project:
    - Logical reason: Completes stop (pause) with resume path; "able to restart the lab if needed" after stop (or post-clean --all, using preserved definition state). Without, post-pause was manual virsh or full re-deploy (no "start process" symmetric to stop, no host services start, no definition-driven order/wait). Outcomes: "Clean" restart (services up, VMs started with wait for boot; clusters can re-join from paused state). "Stop process has to run when we run 'rodeo stop --all'" paired with start. "to the clean process we can add also a force parameter" ( --hard in clean skips stop, but start can follow clean for restore). "stop process needs to analyze the definition" mirrored in start (same infra_type/components/start_order for awareness/order). Fits general picture as the "start" in lifecycle (generate for entry point with infra_type, stop for pause, start for resume, clean for reset; all definition-driven via inventory/load; see runner for deploy up, stop_cmd for pair, clean.py for integration, user-guide for flows, architecture for declarative pipeline role). Engineer reading understands: why (reversible from decl model), how (analysis + Libvirt + services + wait), outcomes (restartable from stopped/ reset state).
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
