"""rodeo plan — read-only diff of desired (plan YAML) vs actual (host) state."""
from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from ..config import ConfigError, load_config, validate_config
from ..drift import DriftReport, collect_drift, inspect_host
from ..profiles import get_profile
from ..state import load_state
from ._options import config_options

console = Console()


@click.command("plan")
@config_options
def plan_cmd(config_path: str, config_dir: str | None, params: tuple[str, ...], paramfile: str | None) -> None:
    """Show what `rodeo deploy` would do, without changing anything."""
    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")
    cfg = load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)
    # Preview is read-only: validation problems are warnings here so the
    # diff is still visible. `rodeo deploy` enforces them strictly.
    try:
        validate_config(cfg)
    except ConfigError as exc:
        console.print(f"\n[yellow]⚠  Config issue (deploy will refuse): {exc}[/yellow]")
    profile = get_profile(cfg.get("type", "suse-virt"))

    actual = _inspect_host(cfg)
    if actual is None:
        console.print("\n[yellow]⚠  libvirt not reachable on host — showing desired state only.[/yellow]")
    report = collect_drift(cfg, actual=actual)
    create, change, ok = _print_vms(report)
    _print_network(report)
    downloads = _print_storage(cfg)
    # A VM create/change the diff above just reported means the "vms" phase's
    # cached "done" state no longer reflects the host — flag that instead of
    # printing a plain checkmark that contradicts the diff a few lines up.
    pending = _print_phases(cfg, profile, vms_drift=report.vms_plan_drift)

    console.print()
    if create or change or downloads or pending:
        parts = []
        if create:
            parts.append(f"[green]{create} to create[/green]")
        if change:
            parts.append(f"[yellow]{change} to change (recreate via rodeo clean + deploy)[/yellow]")
        if ok:
            parts.append(f"{ok} unchanged")
        if downloads:
            parts.append(f"{downloads} download(s)")
        console.print(f"  Plan: {', '.join(parts)} — {pending} phase(s) pending.")
        console.print("  Run [bold]rodeo deploy[/bold] to apply.\n")
    else:
        console.print("  [bold green]Nothing to do — deployment matches the plan.[/bold green]\n")


def _inspect_host(cfg: dict) -> dict | None:
    """Shim so tests can monkeypatch plan_cmd._inspect_host (delegates to drift)."""
    return inspect_host(cfg)


def _print_vms(report: DriftReport) -> tuple[int, int, int]:
    console.print("\n[bold]  Virtual machines[/bold]")
    for c in report.vm_changes:
        line = f"    {c.name:<12} {c.desired:<22} {c.ip:<16}"
        if c.kind == "create":
            console.print(f"{line} [green]+ create[/green]")
        elif c.kind == "memory":
            console.print(
                f"{line} [yellow]~ memory {c.from_value} → {c.to_value} MiB[/yellow]"
            )
        elif c.kind == "vcpu":
            console.print(f"{line} [yellow]~ vcpu {c.from_value} → {c.to_value}[/yellow]")
    for name, desired, ip, state in report.vm_ok:
        line = f"    {name:<12} {desired:<22} {ip:<16}"
        if state == "desired":
            console.print(f"{line} [dim]desired[/dim]")
        else:
            console.print(f"{line} [dim]✓ {state}[/dim]")
    return report.create_count, report.change_count, report.ok_count


def _print_network(report: DriftReport) -> None:
    console.print("\n[bold]  Network[/bold]")
    if not report.reachable:
        console.print("    default (virbr0)  [dim]desired[/dim]")
    elif report.net_active:
        console.print("    default (virbr0)  [dim]✓ active[/dim]")
    else:
        console.print(
            "    default (virbr0)  [green]+ define/start[/green] [dim](vms/cluster phases)[/dim]"
        )


def _print_storage(cfg: dict) -> int:
    image_dir = Path(cfg["storage"]["image_dir"])
    version = cfg.get("versions", {}).get("harvester", "?")
    pxe_root = Path("/srv/harvester-pxe")
    artifacts = [f"harvester-v{version}-amd64.iso"]
    artifacts += [f"{name}-vda.qcow2" for name in cfg.get("vms", {})]
    pxe_artifacts = [
        ("ipxe.efi", Path("/var/lib/libvirt/dnsmasq/ipxe.efi")),
        (f"harvester-v{version}-vmlinuz-amd64", pxe_root / "harvester" / f"harvester-v{version}-vmlinuz-amd64"),
        (f"harvester-v{version}-initrd-amd64", pxe_root / "harvester" / f"harvester-v{version}-initrd-amd64"),
        (f"harvester-v{version}-rootfs-amd64.squashfs", pxe_root / "harvester" / f"harvester-v{version}-rootfs-amd64.squashfs"),
    ]

    console.print(f"\n[bold]  Storage[/bold] [dim]({image_dir})[/dim]")
    downloads = 0
    for artifact in artifacts:
        if (image_dir / artifact).exists():
            console.print(f"    {artifact:<34} [dim]✓ present[/dim]")
        else:
            verb = "+ download" if artifact.endswith(".iso") else "+ create"
            console.print(f"    {artifact:<34} [green]{verb}[/green]")
            downloads += 1

    console.print("\n[bold]  PXE boot[/bold] [dim](pxe_server phase)[/dim]")
    for label, path in pxe_artifacts:
        if path.exists():
            console.print(f"    {label:<34} [dim]✓ present[/dim]")
        else:
            console.print(f"    {label:<34} [green]+ provision[/green]")
            downloads += 1
    return downloads


def _print_phases(cfg: dict, profile, vms_drift: bool = False) -> int:
    plan_name = cfg.get("name", "default")
    state = load_state(plan_name).get("phases", {})
    guard = cfg.get("deployment_target") == "instruqt"

    console.print("\n[bold]  Phases[/bold]")
    pending = 0
    for phase in profile.phases:
        info = state.get(phase, {})
        if info.get("completed") and phase == "vms" and vms_drift:
            console.print(
                f"    [yellow]~[/yellow]  {phase:<10} "
                f"[yellow]done, but drift detected (see Virtual machines above)[/yellow]"
            )
        elif info.get("completed"):
            console.print(f"    [green]✓[/green]  {phase:<10} [dim]done[/dim]")
        elif guard and phase in profile.guarded_phases:
            console.print(
                f"    [yellow]⊘[/yellow]  {phase:<10} [dim]guarded (instruqt) — needs --finalise[/dim]"
            )
        else:
            marker = "[red]✗[/red]" if info.get("last_error") else "[dim]○[/dim]"
            console.print(f"    {marker}  {phase:<10} [dim]pending[/dim]")
            pending += 1
    return pending
