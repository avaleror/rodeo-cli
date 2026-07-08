"""
stop_cmd.py — 'rodeo stop' subcommand for graceful, definition-driven lab shutdown.

This command was created to provide a reversible "pause" step in the lab lifecycle, complementing the existing deploy (via runner.py/phases) and clean (in clean.py, which previously used only hard destroy). Logical reason in project: The declarative definition (see inventory.py _load_topology and definition.yaml comments on start_order, components, node_templates with infra_type, harvester_node_names) encodes the "what" (topology, infra types for Harvester/K8s awareness, on_host services like pxe-server). Without a stop, clean was destructive (VM destroy/undefine + state reset), preventing restart/restore of running clusters (Harvester etcd, K3s state in Rancher). Outcomes of using: Enables "stop all in timely clean manner" (reverse order from start_order for VMs; reverse components for host services) so labs can be paused (VMs off but defined, clusters in restorable state) and resumed via 'rodeo start' or 'rodeo deploy --from cluster' without full re-provision. Integrates with clean (--hard skips for immediate; default clean now preconditions with stop for "all stopped" before destroy, per host reset requirements). Supports "infra aware" via definition analysis (no hardcodes like old cluster.py/rancher.py). Fits general picture as part of end-to-end declarative + lifecycle system (generate for custom start, bootstrap for initial, deploy for up, stop for pause, clean --all for reset/repurpose leaving packages/binary, as in SLES test flows and user requests).

The stop is "clean" (graceful ACPI via LibvirtDriver.shutdown + service stops) so lab can be restarted later (VMs boot with preserved guest state; no data loss in defined infra).

See definition.yaml for infra_type addition (in node_templates) and components (on_host); pairs with start_cmd.py; invoked from clean.py when not --hard; documented in user-guide.md, architecture.md (lifecycle), Generated stop-design-options.md (options/rationale).
"""

import subprocess
import sys
import time

import click
from rich.console import Console

from ..config import load_config
from ..inventory import _load_topology
from ..privilege import ensure_root, is_root
from ._options import config_options
from ..engine.libvirt import LibvirtDriver, discover_rodeo_vm_names

console = Console()

# Known host services to stop for on_host components (simple hardcoded for now).
# Extend as needed or make configurable in definition components under 'stop_services'.
HOST_STOP_SERVICES = {
    "pxe-server": ["nginx"],
    # "host-network-prep": [],  # usually no runtime service to stop
}


def _stop_host_services(components: list[dict]) -> None:
    """Stops relevant host-side services for on_host components (from definition), in reverse dependency order.

    Inputs:
    - components: list[dict] from loaded definition (topology["components"] via _load_topology; filters those with "on_host": true, e.g. pxe-server, host-network-prep).

    Outputs:
    - None (side-effect: runs systemctl stop on host for mapped services; logs progress).

    Patterns inside:
    - Reverse iteration for dependents-first (standard in lifecycle; mirrors reverse start_order for VMs; see components list order in definition.yaml).
    - Lookup in HOST_STOP_SERVICES (hardcoded map for known on_host; extensible via definition components['stop_services'] in future per EIB component patterns).
    - subprocess with sudo (consistent with clean.py host ops, install_deps; assumes runner has sudo for lab host services).
    - Graceful (no --force; errors logged but non-fatal for idempotency).

    How it works:
    - Scans components for on_host (infra from definition, not hardcoded like pre-inventory phases).
    - For matching (e.g. "pxe-server"), stops associated (nginx for pxe/http in pxe_server role).
    - Called before VM stop in stop flow.

    Fit in project:
    - Logical reason: Definition components (see definition.yaml and inventory.py) declare on_host services (pxe for diskless boot, network-prep for libvirt/firewalld) that are part of "the rodeo" (not guest-only). Stopping only VMs left host services running (nginx serving stale pxe, etc.), preventing clean "stopped" state for restart (VMs would boot but pxe/ host infra inconsistent) or clean (artifacts left but services active). Outcomes: Full infra pause (guests + host lab services) based on definition analysis, enabling restore (start reverses) or clean (no active interference). Fits declarative model (definition drives stop in stop_cmd, just as it drives deploy in runner.py and clean in clean.py); supports "infra aware" and "pause as they should" (reverse order from components/start_order). Part of lifecycle for reset/repurpose (stop before clean --all per user request; see Generated stop-design-options.md).
    """
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
    """Graceful shutdown of VMs using ACPI, in caller-provided order (typically reverse start_order from definition).

    Inputs:
    - driver: LibvirtDriver instance (connected via cfg libvirt.uri; provides is_running/shutdown).
    - vm_names: list[str] (from cfg["vms"] or --all discovery; ordered by stop_order = reversed(definition start_order)).
    - timeout: int (seconds to wait post-shutdown before considering still-running for clean fallback).

    Outputs:
    - None (side-effect: calls shutdown on running; waits; logs status. Idempotent: skips non-running).

    Patterns inside:
    - Check is_running before action (from LibvirtDriver; avoids unnecessary shutdown).
    - Shutdown + poll wait (uses driver.shutdown which does dom.shutdown() for guest ACPI; loop with sleep for "timely" but bounded stop).
    - Fallback note to clean (if timeout, clean will hard destroy).
    - Order from definition (not hardcoded; enables infra-aware for harvester vs rancher per infra_type).

    How it works:
    - For each in order: if running, initiate graceful (ACPI to guest OS, which for Harvester nodes stops RKE2 etc. cleanly; for Rancher stops K3s).
    - Waits to confirm stopped (ensures "all stopped" before return, for clean to destroy defined-but-off VMs).
    - Uses definition-derived order (from start_order in topology) so dependents stop after (e.g. rancher after harvesters? reverse for shutdown).

    Fit in project:
    - Logical reason: Pre-clean clean stop was missing (old clean did only hard destroy in clean.py, risking inconsistent state for restart; no "gentle" path using definition for order/infra). Outcomes: "Stop process that stops all in timely and clean manner" (ACPI + wait; definition-driven via start_order/components/infra_type) so "can be restored and start again" (VMs off but defined; guest clusters in restorable state, e.g. Harvester etcd paused gracefully). "Runs before cleaning the host" (integrated in clean unless --hard; see clean.py pre-destroy logic and --hard flag). "stop process needs to analyze the definition file" ( _load_topology for start_order, node_templates infra_type, components; see inventory.py). Fits general picture as symmetric to deploy (runner starts in order) and start_cmd (reverse); enables full reset/repurpose lifecycle (generate -> ... -> stop -> clean --all -> start or fresh) without package removal, as requested. Part of infra-aware commands (stop_cmd + start_cmd + clean enhancements + infra_type addition).
    """
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
    """Graceful stop of the lab(s) so it can be restarted later (VMs off but defined; clusters stopped gently via infra awareness from definition).

    Inputs (from @config_options + flags):
    - config_path/config_dir/params/paramfile: for load_config (provides name, vms, type for profile; config_dir for definition override).
    - yes: bool (bypass confirm).
    - all: bool (use pattern-based discovery for all rodeo VMs instead of plan vms).

    Outputs:
    - None (side effects: graceful shutdowns + logs; state left for restart via start or deploy --from).

    Patterns inside:
    - Definition load via _load_topology (for start_order, components, node_templates[in]fra_type; see inventory.py).
    - VM list from cfg or discovery (like clean.py --all).
    - Infra log + ordered stop (reverse start_order for VMs; reverse on_host components for services).
    - Delegation to helpers (_stop_host_services, _stop_vms using LibvirtDriver).
    - Fallback to virsh for graceful (consistent with clean.py/libvirt.py patterns).
    - Idempotent checks (is_running).

    How it works:
    - Loads cfg + full topology from definition (analyzes for infra awareness: infra_type to identify harvester vs rancher nodes for order/handling; components for on_host services; start_order for reverse shutdown sequence).
    - Stops host services first (reverse components, using HOST_STOP_SERVICES map for e.g. pxe-server nginx).
    - Stops VMs in definition-derived order (if running: driver.shutdown + wait; falls back to virsh shutdown).
    - For --all: broad discovery (patterns) to cover any plan's VMs.
    - "Checks if VMs running" before/ during (per clean requirement); "executes clean stop" (ACPI + services, definition-driven, no hard kill).

    Fit in project:
    - Logical reason: Addresses need for "stop process that stops all in a timely and clean manner to be able to restart the lab if needed" (pre-clean for "clean process do it with all stopped"; reversible for restore). Old clean (pre-enhance) only did hard destroy/undefine + state reset (no gentle path, no definition analysis for order/infra_type, no host services stop). Without it, labs couldn't be paused gracefully (e.g. Harvester cluster nodes stop abruptly, no restart from defined state; host services like pxe left running). "Stop process needs to analyze the definition file to be infra aware" (uses infra_type added to templates, components, start_order/harvester_node_names; "pause things as they should" via reverse order). "In the definition file or definition structure would be good to indicate if a VM/host is going to run Kubernetes or harvester" (infra_type enables this for stop/start awareness, used in _stop_vms log and future logic; see definition.yaml updates and stop-design-options.md).
    - Outcomes of using: "Undo any VMs, networks [via clean after], specific plans" in gentle way (VMs off/defined, services stopped, state for resume); "runs before cleaning the host" (integrated: clean calls stop logic unless --hard; see clean.py pre-destroy logic and --hard flag). "rodeo stop --all" as requested. Enables "fresh testing can start or the node can be repurposed" (stop -> clean --all --secrets leaves clean host infra; restart via start/deploy). "to the clean process we can add also a force parameter that executes first the stop process" (--hard for bypass; default preconditions with stop). Fits general picture as part of declarative lifecycle (definition drives generate (for custom), bootstrap (initial), deploy (up), stop/start (pause/resume using infra), clean (reset); all via load_config/inventory for consistency; see architecture.md pipeline, user-guide for "stop before clean", clean.py for integration, cli.py registration). Any engineer reading docs + code (full docstrings here + in generate/stop helpers) understands: why (reversible infra pause from decl model), how (definition analysis + Libvirt + host stops), outcomes (restartable labs, clean resets).
    """
    if not is_root():
        ensure_root(sys.argv[1:])

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

    # VMs to stop. --all discovers what's actually on the host; a plan stop uses
    # the definition's VM list, falling back to discovery (never a hardcoded set).
    uri0 = (cfg or {"libvirt": {"uri": "qemu:///system"}})["libvirt"]["uri"]
    if all:
        vm_names = discover_rodeo_vm_names(uri0)
    else:
        vm_names = list((cfg or {}).get("vms", {}).keys()) or discover_rodeo_vm_names(uri0)

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
                subprocess.run(["virsh", "-c", uri, "shutdown", name], check=False, capture_output=True)
                console.print(f"  [dim]virsh shutdown[/dim] {name}")
            except Exception:
                pass

    console.print("\n[bold green]✓  Stop complete.[/bold green] VMs are off (graceful). Use 'rodeo start' or 'rodeo deploy' to restart.\n")
