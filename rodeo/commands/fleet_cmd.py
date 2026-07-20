"""rodeo fleet — fan-out doctor/status across a workshop host inventory."""
from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from ..config import ConfigError
from ..fleet.doctor import fleet_doctor
from ..fleet.inventory import load_inventory, parse_label_opts, select_hosts
from ..fleet.status import fleet_status

console = Console()


@click.group("fleet")
def fleet_cmd() -> None:
    """Fan-out read-only checks across workshop KVM hosts (OpenSSH)."""


def _inventory_options(fn):
    """Shared -f / --label / --host / -j / --output (applied closest-first)."""
    fn = click.option(
        "-f",
        "--file",
        "inventory_path",
        required=True,
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Path to workshop.yaml inventory.",
    )(fn)
    fn = click.option(
        "--label",
        "labels",
        multiple=True,
        metavar="KEY=VALUE",
        help="Filter hosts by label (repeatable, AND).",
    )(fn)
    fn = click.option(
        "--host",
        "host_ids",
        multiple=True,
        metavar="ID",
        help="Limit to host id (repeatable).",
    )(fn)
    fn = click.option(
        "-j",
        "--concurrency",
        default=8,
        show_default=True,
        type=click.IntRange(1, 64),
        help="Max parallel SSH sessions.",
    )(fn)
    fn = click.option(
        "--output",
        "output_fmt",
        type=click.Choice(["text", "json"], case_sensitive=False),
        default="text",
        show_default=True,
        help="Output format.",
    )(fn)
    return fn


@fleet_cmd.command("doctor")
@_inventory_options
def fleet_doctor_cmd(
    output_fmt: str,
    concurrency: int,
    host_ids: tuple[str, ...],
    labels: tuple[str, ...],
    inventory_path: Path,
) -> None:
    """Run ``rodeo doctor --output json`` on each selected host."""
    try:
        inventory = load_inventory(inventory_path)
        hosts = select_hosts(
            inventory,
            ids=list(host_ids) or None,
            labels=parse_label_opts(labels) or None,
        )
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    results = fleet_doctor(inventory, hosts, concurrency=concurrency)
    payload = {
        "workshop": inventory.name,
        "hosts": [
            {
                "id": r.id,
                "ok": r.ok,
                "error": r.error,
                "report": r.report,
            }
            for r in results
        ],
    }

    if output_fmt == "json":
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        table = Table(title=f"Fleet doctor — {inventory.name}", show_header=True)
        table.add_column("id", style="bold")
        table.add_column("ok")
        table.add_column("profile")
        table.add_column("cpus")
        table.add_column("detail", overflow="fold")
        for r in results:
            if r.ok and r.report:
                host = r.report.get("host") or {}
                table.add_row(
                    r.id,
                    "[green]yes[/green]",
                    str(r.report.get("recommended_profile", "")),
                    str(host.get("cpus", "")),
                    "fits" if r.report.get("profile_fits") else "undersized",
                )
            else:
                table.add_row(
                    r.id,
                    "[red]no[/red]",
                    "—",
                    "—",
                    (r.error or "")[:120],
                )
        console.print()
        console.print(table)
        console.print()

    if any(not r.ok for r in results):
        raise SystemExit(1)


@fleet_cmd.command("status")
@_inventory_options
def fleet_status_cmd(
    output_fmt: str,
    concurrency: int,
    host_ids: tuple[str, ...],
    labels: tuple[str, ...],
    inventory_path: Path,
) -> None:
    """Run ``rodeo status --output json`` on each selected host (in lab.dir)."""
    try:
        inventory = load_inventory(inventory_path)
        hosts = select_hosts(
            inventory,
            ids=list(host_ids) or None,
            labels=parse_label_opts(labels) or None,
        )
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    results = fleet_status(inventory, hosts, concurrency=concurrency)
    payload = {
        "workshop": inventory.name,
        "lab_dir": inventory.lab_dir,
        "hosts": [
            {
                "id": r.id,
                "ok": r.ok,
                "error": r.error,
                "report": r.report,
            }
            for r in results
        ],
    }

    if output_fmt == "json":
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        table = Table(title=f"Fleet status — {inventory.name}", show_header=True)
        table.add_column("id", style="bold")
        table.add_column("ok")
        table.add_column("lab")
        table.add_column("vip")
        table.add_column("phases", overflow="fold")
        table.add_column("detail", overflow="fold")
        for r in results:
            if r.ok and r.report:
                phases = r.report.get("phases") or {}
                done = sum(1 for p in phases.values() if p.get("completed"))
                total = len(phases)
                vip_ok = r.report.get("vip_reachable")
                vip = r.report.get("vip", "")
                vip_s = f"{vip} ({'up' if vip_ok else 'down'})" if vip else "—"
                table.add_row(
                    r.id,
                    "[green]yes[/green]",
                    str(r.report.get("name", "")),
                    vip_s,
                    f"{done}/{total}",
                    "",
                )
            else:
                table.add_row(
                    r.id,
                    "[red]no[/red]",
                    "—",
                    "—",
                    "—",
                    (r.error or "")[:120],
                )
        console.print()
        console.print(table)
        console.print()

    if any(not r.ok for r in results):
        raise SystemExit(1)
