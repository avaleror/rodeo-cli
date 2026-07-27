"""rodeo fleet — fan-out doctor/status/deploy across workshop KVM hosts."""
from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from ..config import ConfigError
from ..fleet.access import access_payload, fleet_access
from ..fleet.deploy import fleet_deploy, refresh_job_from_status, results_payload
from ..fleet.diagnose import (
    diagnose_outdir,
    diagnose_payload,
    fleet_diagnose,
    select_diagnose_hosts,
)
from ..fleet.doctor import fleet_doctor
from ..fleet.inventory import (
    load_inventory,
    parse_label_opts,
    require_deploy_config,
    select_hosts,
)
from ..fleet.job import job_path_for, load_job
from ..fleet.provision import (
    deprovision_payload,
    fleet_deprovision,
    fleet_provision,
    provision_payload,
)
from ..fleet.status import fleet_status

console = Console()


@click.group("fleet")
def fleet_cmd() -> None:
    """Fan-out checks and deploys across workshop KVM hosts (OpenSSH)."""


def _file_label_host(fn):
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
    return fn


def _output_opt(fn):
    return click.option(
        "--output",
        "output_fmt",
        type=click.Choice(["text", "json"], case_sensitive=False),
        default="text",
        show_default=True,
        help="Output format.",
    )(fn)


def _concurrency_opt(default: int | None = 8):
    def deco(fn):
        return click.option(
            "-j",
            "--concurrency",
            default=default,
            show_default=default is not None,
            type=click.IntRange(1, 64),
            help="Max parallel SSH sessions "
            + ("(default: lab.concurrency or 4 for deploy)." if default is None else ""),
        )(fn)

    return deco


def _load_selection(
    inventory_path: Path,
    labels: tuple[str, ...],
    host_ids: tuple[str, ...],
):
    inventory = load_inventory(inventory_path)
    hosts = select_hosts(
        inventory,
        ids=list(host_ids) or None,
        labels=parse_label_opts(labels) or None,
    )
    return inventory, hosts


@fleet_cmd.command("doctor")
@_output_opt
@_concurrency_opt(8)
@_file_label_host
def fleet_doctor_cmd(
    inventory_path: Path,
    labels: tuple[str, ...],
    host_ids: tuple[str, ...],
    concurrency: int,
    output_fmt: str,
) -> None:
    """Run ``rodeo doctor --output json`` on each selected host."""
    try:
        inventory, hosts = _load_selection(inventory_path, labels, host_ids)
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    results = fleet_doctor(inventory, hosts, concurrency=concurrency)
    payload = {
        "workshop": inventory.name,
        "hosts": [
            {"id": r.id, "ok": r.ok, "error": r.error, "report": r.report}
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
                table.add_row(r.id, "[red]no[/red]", "—", "—", (r.error or "")[:120])
        console.print()
        console.print(table)
        console.print()

    if any(not r.ok for r in results):
        raise SystemExit(1)


@fleet_cmd.command("status")
@_output_opt
@_concurrency_opt(8)
@_file_label_host
def fleet_status_cmd(
    inventory_path: Path,
    labels: tuple[str, ...],
    host_ids: tuple[str, ...],
    concurrency: int,
    output_fmt: str,
) -> None:
    """Run ``rodeo status --output json`` on each selected host (in lab.dir)."""
    try:
        inventory, hosts = _load_selection(inventory_path, labels, host_ids)
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    results = fleet_status(inventory, hosts, concurrency=concurrency)
    # Refresh job file if present so retry sees current states
    job_file = job_path_for(inventory_path)
    if job_file.is_file():
        try:
            job = load_job(job_file)
            refresh_job_from_status(
                inventory,
                hosts,
                job,
                inventory_path=inventory_path,
                concurrency=concurrency,
            )
        except ConfigError:
            pass

    payload = {
        "workshop": inventory.name,
        "lab_dir": inventory.lab_dir,
        "hosts": [
            {"id": r.id, "ok": r.ok, "error": r.error, "report": r.report}
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
                    r.id, "[red]no[/red]", "—", "—", "—", (r.error or "")[:120]
                )
        console.print()
        console.print(table)
        console.print()

    if any(not r.ok for r in results):
        raise SystemExit(1)


@fleet_cmd.command("deploy")
@_output_opt
@_concurrency_opt(None)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-start even when remote phases are already complete.",
)
@_file_label_host
def fleet_deploy_cmd(
    inventory_path: Path,
    labels: tuple[str, ...],
    host_ids: tuple[str, ...],
    concurrency: int | None,
    output_fmt: str,
    force: bool,
) -> None:
    """Bootstrap, sync lab, and start ``rodeo up`` in tmux on each host.

    Returns after starts succeed; use ``rodeo fleet status`` to poll convergence.
    Writes ``<inventory>.job.yaml`` beside the inventory file.
    """
    try:
        inventory, hosts = _load_selection(inventory_path, labels, host_ids)
        require_deploy_config(inventory)
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    results, _job, job_path = fleet_deploy(
        inventory,
        hosts,
        inventory_path=inventory_path,
        concurrency=concurrency,
        force=force,
    )
    payload = results_payload(inventory.name, results, job_path)

    if output_fmt == "json":
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        table = Table(title=f"Fleet deploy — {inventory.name}", show_header=True)
        table.add_column("id", style="bold")
        table.add_column("state")
        table.add_column("tmux")
        table.add_column("detail", overflow="fold")
        for r in results:
            style = {
                "running": "cyan",
                "skipped": "green",
                "failed": "red",
            }.get(r.state, "white")
            table.add_row(
                r.id,
                f"[{style}]{r.state}[/{style}]",
                r.tmux or "—",
                (r.error or r.detail or "")[:120],
            )
        console.print()
        console.print(table)
        console.print(f"\n  Job file: [cyan]{job_path}[/cyan]")
        console.print(
            "  Poll: [bold]rodeo fleet status -f "
            f"{inventory_path}[/bold]\n"
        )

    if any(not r.ok for r in results):
        raise SystemExit(1)


@fleet_cmd.command("retry")
@_output_opt
@_concurrency_opt(None)
@click.option(
    "--failed-only/--all-selected",
    default=True,
    help="Retry only hosts marked failed in the job file (default), "
    "or all hosts matching --label/--host.",
)
@_file_label_host
def fleet_retry_cmd(
    inventory_path: Path,
    labels: tuple[str, ...],
    host_ids: tuple[str, ...],
    concurrency: int | None,
    output_fmt: str,
    failed_only: bool,
) -> None:
    """Re-run deploy for failed (or selected) hosts; updates the job file."""
    try:
        inventory, selected = _load_selection(inventory_path, labels, host_ids)
        require_deploy_config(inventory)
        job_file = job_path_for(inventory_path)
        job = load_job(job_file)
        # Refresh states from live status before choosing failures
        refresh_job_from_status(
            inventory,
            selected,
            job,
            inventory_path=inventory_path,
            concurrency=concurrency or inventory.deploy_concurrency,
        )
        job = load_job(job_file)
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    if failed_only:
        failed = set(job.failed_ids())
        hosts = [h for h in selected if h.id in failed]
        if not hosts:
            console.print("[green]No failed hosts to retry.[/green]")
            if output_fmt == "json":
                click.echo(
                    json.dumps(
                        {"workshop": inventory.name, "hosts": [], "job_file": str(job_file)},
                        indent=2,
                    )
                )
            raise SystemExit(0)
    else:
        hosts = selected

    results, _job, job_path = fleet_deploy(
        inventory,
        hosts,
        inventory_path=inventory_path,
        concurrency=concurrency,
        force=True,
        merge_job=job,
    )
    payload = results_payload(inventory.name, results, job_path)

    if output_fmt == "json":
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        table = Table(title=f"Fleet retry — {inventory.name}", show_header=True)
        table.add_column("id", style="bold")
        table.add_column("state")
        table.add_column("detail", overflow="fold")
        for r in results:
            table.add_row(r.id, r.state, (r.error or r.detail or "")[:120])
        console.print()
        console.print(table)
        console.print(f"\n  Job file: [cyan]{job_path}[/cyan]\n")

    if any(not r.ok for r in results):
        raise SystemExit(1)


@fleet_cmd.command("access")
@_output_opt
@_file_label_host
def fleet_access_cmd(
    inventory_path: Path,
    labels: tuple[str, ...],
    host_ids: tuple[str, ...],
    output_fmt: str,
) -> None:
    """Print student UI URLs (Harvester / Rancher DNAT). Never prints passwords."""
    try:
        inventory, hosts = _load_selection(inventory_path, labels, host_ids)
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    rows = fleet_access(inventory, hosts)
    payload = access_payload(inventory.name, rows)

    if output_fmt == "json":
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        table = Table(title=f"Fleet access — {inventory.name}", show_header=True)
        table.add_column("id", style="bold")
        table.add_column("Harvester")
        table.add_column("Rancher")
        table.add_column("note", overflow="fold")
        for r in rows:
            table.add_row(
                r.id,
                r.harvester_url or "—",
                r.rancher_url or "—",
                (r.note or "")[:80],
            )
        console.print()
        console.print(table)
        console.print(
            "\n  [dim]Passwords stay on each host in ~/.rodeo/secrets.yaml[/dim]\n"
        )


@fleet_cmd.command("diagnose")
@_output_opt
@_concurrency_opt(8)
@click.option(
    "--failed-only/--all-selected",
    default=True,
    help="Collect only failed/problematic hosts (default), or every selected host.",
)
@click.option(
    "-o",
    "--outdir",
    "outdir_opt",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Local directory for artifacts (default: <inventory>.diagnose-<utc>).",
)
@click.option(
    "--lines",
    default=500,
    show_default=True,
    type=click.IntRange(50, 20000),
    help="Tail lines per remote log file.",
)
@_file_label_host
def fleet_diagnose_cmd(
    inventory_path: Path,
    labels: tuple[str, ...],
    host_ids: tuple[str, ...],
    concurrency: int,
    output_fmt: str,
    failed_only: bool,
    outdir_opt: Path | None,
    lines: int,
) -> None:
    """Collect remote status JSON + log tails onto the laptop for forensics.

    Pulls ``rodeo status --output json``, ``~/.rodeo/logs/*.log`` tails,
    phase state YAML, and optional tmux pane capture. Writes one directory
    per host under ``-o`` / the default diagnose folder.
    """
    try:
        inventory, hosts = _load_selection(inventory_path, labels, host_ids)
        hosts, job, _ = select_diagnose_hosts(
            hosts,
            inventory=inventory,
            inventory_path=inventory_path,
            failed_only=failed_only,
            concurrency=concurrency,
            timeout=120.0,
        )
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    if not hosts:
        console.print("[green]No hosts need diagnose (nothing failed).[/green]")
        if output_fmt == "json":
            click.echo(
                json.dumps(
                    {
                        "workshop": inventory.name,
                        "outdir": None,
                        "hosts": [],
                    },
                    indent=2,
                )
            )
        raise SystemExit(0)

    outdir = diagnose_outdir(inventory_path, outdir_opt)
    results, outdir = fleet_diagnose(
        inventory,
        hosts,
        inventory_path=inventory_path,
        outdir=outdir,
        concurrency=concurrency,
        lines=lines,
        job=job,
    )
    payload = diagnose_payload(inventory.name, outdir, results)

    if output_fmt == "json":
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        table = Table(title=f"Fleet diagnose — {inventory.name}", show_header=True)
        table.add_column("id", style="bold")
        table.add_column("collect")
        table.add_column("attention")
        table.add_column("phases", overflow="fold")
        table.add_column("detail", overflow="fold")
        for r in results:
            phases = ",".join(r.failed_phases) if r.failed_phases else "—"
            detail = r.error or r.job_error or r.status_error or ""
            table.add_row(
                r.id,
                "[green]ok[/green]" if r.ok else "[red]fail[/red]",
                "[yellow]yes[/yellow]" if r.needs_attention else "no",
                phases,
                detail[:100],
            )
        console.print()
        console.print(table)
        console.print(f"\n  Artifacts: [cyan]{outdir}[/cyan]")
        console.print(
            "  Per host: status.json, logs/, meta/ "
            "(state YAML, tmux pane), summary.json\n"
        )

    # Exit 1 only when collection itself failed; phase errors are the
    # forensic payload (needs_attention) and still count as a successful pull.
    if any(not r.ok for r in results):
        raise SystemExit(1)


@fleet_cmd.command("provision")
@_output_opt
@click.option(
    "--no-wait-ssh",
    is_flag=True,
    default=False,
    help="Do not wait for SSH after instances are running.",
)
@click.option(
    "--no-write",
    is_flag=True,
    default=False,
    help="Do not merge hosts into workshop.yaml.",
)
@click.option(
    "--host",
    "host_ids",
    multiple=True,
    metavar="ID",
    help="Limit to host id (repeatable). Default: hosts: or provider.count.",
)
@click.option(
    "-f",
    "--file",
    "inventory_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to workshop.yaml inventory.",
)
def fleet_provision_cmd(
    inventory_path: Path,
    host_ids: tuple[str, ...],
    output_fmt: str,
    no_wait_ssh: bool,
    no_write: bool,
) -> None:
    """Create or reuse cloud KVM hosts (F4); merge into workshop.yaml.

    Requires ``provider:`` in the inventory (AWS F4a). Install optional deps:
    ``pip install 'rodeo-cli[aws]'``.
    """
    try:
        inventory = load_inventory(inventory_path)
        hosts = fleet_provision(
            inventory,
            inventory_path,
            host_ids=list(host_ids) or None,
            wait_ssh=not no_wait_ssh,
            write_inventory=not no_write,
        )
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    payload = provision_payload(inventory.name, hosts, inventory_path=inventory_path)
    if output_fmt == "json":
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    table = Table(title=f"Fleet provision — {inventory.name}", show_header=True)
    table.add_column("id", style="bold")
    table.add_column("action")
    table.add_column("ip")
    table.add_column("instance")
    for h in hosts:
        table.add_row(
            h.id,
            h.labels.get("provision_action", "—"),
            h.public_ip,
            h.provider_id or "—",
        )
    console.print()
    console.print(table)
    if not no_write:
        console.print(f"\n  Updated: [cyan]{inventory_path}[/cyan]")
    console.print("  Next: [bold]rodeo fleet doctor -f …[/bold] then [bold]deploy[/bold]\n")


@fleet_cmd.command("deprovision")
@_output_opt
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Required — refuse to terminate without explicit confirmation.",
)
@click.option(
    "--host",
    "host_ids",
    multiple=True,
    metavar="ID",
    help="Limit to host id (repeatable).",
)
@click.option(
    "-f",
    "--file",
    "inventory_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to workshop.yaml inventory.",
)
def fleet_deprovision_cmd(
    inventory_path: Path,
    host_ids: tuple[str, ...],
    output_fmt: str,
    yes: bool,
) -> None:
    """Terminate ownership-tagged cloud instances for this workshop (F4)."""
    if not yes:
        console.print(
            "[red]✗  Refusing to deprovision without --yes "
            "(destroys tagged cloud instances).[/red]"
        )
        raise SystemExit(1)
    try:
        inventory = load_inventory(inventory_path)
        results = fleet_deprovision(
            inventory,
            host_ids=list(host_ids) or None,
        )
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    payload = deprovision_payload(inventory.name, results)
    if output_fmt == "json":
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        table = Table(title=f"Fleet deprovision — {inventory.name}", show_header=True)
        table.add_column("id", style="bold")
        table.add_column("ok")
        table.add_column("instance")
        table.add_column("detail", overflow="fold")
        for r in results:
            table.add_row(
                r.id,
                "[green]yes[/green]" if r.ok else "[red]no[/red]",
                r.provider_id or "—",
                (r.error or r.detail or "")[:100],
            )
        console.print()
        console.print(table)
        console.print()

    if any(not r.ok for r in results):
        raise SystemExit(1)
