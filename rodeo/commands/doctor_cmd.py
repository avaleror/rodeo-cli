"""rodeo doctor — is this host ready, and which lab fits?

A friendly, read-only host check. Run it before anything else: it reports RAM, CPU,
disk, KVM, nested virt, and required tools, then recommends the largest lab profile
that fits. ``rodeo up`` runs the same checks automatically.
"""
from __future__ import annotations

import json

import click
from rich.console import Console
from rich.table import Table

from ..preflight import (
    CORE_PY_MODULES,
    CORE_TOOLS,
    OPTIONAL_TOOLS,
    PROFILE_SIZING,
    detect_host,
    profile_label,
    recommend_profile,
)
from ..service.doctor import doctor_report

console = Console()


@click.command("doctor")
@click.option(
    "--output",
    "output_fmt",
    type=click.Choice(["text", "json"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format.",
)
def doctor_cmd(output_fmt: str) -> None:
    """Check host readiness and recommend a lab that fits."""
    if output_fmt == "json":
        click.echo(json.dumps(doctor_report(), indent=2, sort_keys=True))
        return

    host = detect_host()

    facts = Table(title="Host", show_header=False, box=None)
    facts.add_column(style="dim")
    facts.add_column()
    ram = host["ram_total_gib"]
    facts.add_row(
        "RAM",
        f"{ram} GiB total, {host['ram_avail_gib']} GiB available" if ram else "unknown",
    )
    facts.add_row("CPUs", str(host["cpus"]) if host["cpus"] else "unknown")
    disk = host["disk_free_gib"]
    facts.add_row(
        "Disk",
        f"{disk} GiB free in {host['image_dir']}" if disk >= 0 else "unknown",
    )
    facts.add_row("Package mgr", host["pkg_mgr"])
    console.print()
    console.print(facts)

    checks = Table(title="Requirements", show_header=False, box=None)
    checks.add_column()
    checks.add_column(style="dim")
    checks.add_row(
        _mark(host["has_kvm"], "/dev/kvm"),
        "" if host["has_kvm"] else "KVM not available",
    )
    checks.add_row(
        _mark(host["nested"], "nested virtualization"),
        "" if host["nested"] else "enable in the kvm_intel/kvm_amd module",
    )
    for tool in CORE_TOOLS:
        ok = host["core_tools"][tool]
        checks.add_row(
            _mark(ok, tool),
            "" if ok else "required — run: sudo rodeo install-deps",
        )
    for mod in CORE_PY_MODULES:
        ok = host.get("py_modules", {}).get(mod, False)
        checks.add_row(
            _mark(ok, f"python: {mod}"),
            "" if ok else "required — run: sudo rodeo install-deps",
        )
    for tool in OPTIONAL_TOOLS:
        ok = host["optional_tools"][tool]
        checks.add_row(
            _warn_mark(ok, tool),
            "" if ok else "optional (ssh/attach/fallbacks)",
        )
    console.print()
    console.print(checks)

    rec, fits = recommend_profile(host)
    avail = host.get("ram_avail_gib") or host.get("ram_total_gib") or 0
    console.print()
    console.print("[bold]Lab profiles[/bold]")
    for tier in PROFILE_SIZING:
        marker = "[green]→[/green]" if tier["name"] == rec else " "
        ok = (
            "[green]fits[/green]"
            if avail >= tier["ram_gib"]
            else f"[yellow]needs {tier['ram_gib']} GiB[/yellow]"
        )
        console.print(f"  {marker} [bold]{tier['name']}[/bold]  {tier['label']}  ({ok})")

    console.print()
    if fits:
        console.print(
            f"[bold green]Recommended:[/bold green] [bold]{rec}[/bold] "
            f"({profile_label(rec)}).  Start it with: [bold]rodeo up[/bold]"
        )
    else:
        console.print(
            f"[yellow]No profile fully fits {avail} GiB RAM.[/yellow] "
            f"Smallest is '{rec}' ({PROFILE_SIZING[0]['ram_gib']} GiB). "
            "Add RAM or try a bigger host."
        )

    from ..sizing import INSTRUQT_VCPU_BUDGET_RATIO, instruqt_vcpu_budget

    cpus = host.get("cpus") or 0
    if cpus:
        budget = instruqt_vcpu_budget(cpus)
        pct = int(INSTRUQT_VCPU_BUDGET_RATIO * 100)
        console.print()
        console.print(
            f"[dim]Instruqt tip:[/dim] keep Σ guest vCPU ≤ ~{pct}% of host "
            f"(≤ {budget} on this {cpus}-vCPU machine). "
            f"`rodeo up` applies presets when deployment_target is instruqt."
        )
    console.print()


def _mark(ok: bool, label: str) -> str:
    return f"[green]✓[/green]  {label}" if ok else f"[red]✗[/red]  {label}"


def _warn_mark(ok: bool, label: str) -> str:
    return f"[green]✓[/green]  {label}" if ok else f"[yellow]⚠[/yellow]  {label}"
