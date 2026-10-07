"""rodeo fleet — fan-out doctor/status/deploy across workshop KVM hosts."""
from __future__ import annotations

import json
from dataclasses import replace
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
    FleetInventory,
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
from ..fleet.portal import deprovision_portal, provision_portal
from ..fleet.status import fleet_status
from ..fleet.student_access import fleet_open_access, open_access_payload
from ..install_source import resolve_install_source

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


def _with_ref(inventory: FleetInventory, ref: str | None) -> FleetInventory:
    """Apply ``--ref``, repointing the installer at the same ref it checks out.

    Only a *configured* ``lab.install_url`` is carried over: leaving the
    resolved default in place would fetch install.sh from main while checking
    out the ref — the same class of bug in a subtler form. Exits 1 on an
    invalid ref, before any host is contacted.
    """
    if not ref:
        return inventory
    override: dict[str, str] = {"ref": ref}
    if inventory.install_url_explicit:
        override["install_url"] = inventory.install_url
    try:
        install_url, resolved_ref = resolve_install_source(override)
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)
    return replace(inventory, install_url=install_url, ref=resolved_ref)


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
@click.option(
    "--ref",
    "ref",
    default=None,
    metavar="GIT_REF",
    help="Branch, tag or SHA of rodeo-cli to run on the hosts (overrides lab.ref). "
         "Forces the bootstrap even where rodeo is already installed — the only way "
         "a just-pushed commit reaches hosts that were bootstrapped earlier. "
         "Default: leave each host on the code it already has.",
)
@_file_label_host
def fleet_deploy_cmd(
    inventory_path: Path,
    labels: tuple[str, ...],
    host_ids: tuple[str, ...],
    concurrency: int | None,
    output_fmt: str,
    force: bool,
    ref: str | None,
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

    inventory = _with_ref(inventory, ref)

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
@click.option(
    "--ref",
    "ref",
    default=None,
    metavar="GIT_REF",
    help="Branch, tag or SHA of rodeo-cli to run on the hosts (overrides lab.ref). "
         "Forces the bootstrap even where rodeo is already installed — the only way "
         "a just-pushed commit reaches hosts that were bootstrapped earlier. "
         "Default: leave each host on the code it already has.",
)
@_file_label_host
def fleet_retry_cmd(
    inventory_path: Path,
    labels: tuple[str, ...],
    host_ids: tuple[str, ...],
    concurrency: int | None,
    output_fmt: str,
    failed_only: bool,
    ref: str | None,
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

    inventory = _with_ref(inventory, ref)

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
        extra = list(inventory.ui_ports)
        show_hv = any(r.harvester_url for r in rows) or not extra
        show_rn = any(r.rancher_url for r in rows) or not extra
        if show_hv:
            table.add_column("Harvester")
        if show_rn:
            table.add_column("Rancher")
        for name in extra:
            table.add_column(name)
        table.add_column("note", overflow="fold")
        for r in rows:
            cells = [r.id]
            if show_hv:
                cells.append(r.harvester_url or "—")
            if show_rn:
                cells.append(r.rancher_url or "—")
            cells += [r.other_urls.get(name, "—") for name in extra]
            table.add_row(*cells, (r.note or "")[:80])
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
    "--no-portal",
    is_flag=True,
    default=False,
    help="Skip the claim portal VM even when portal.enabled.",
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
    no_portal: bool,
) -> None:
    """Create or reuse cloud KVM hosts (F4); merge into workshop.yaml.

    Requires ``provider:`` in the inventory (AWS F4a). Install optional deps:
    the ``[aws]`` extra (``pip install -e '.[aws]'`` in the checkout; docs/install.md).
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

    # Claim portal VM (F5.2) after the labs: a portal failure never undoes them,
    # it reports and exits non-zero so a re-run converges.
    portal_error: str | None = None
    portal = inventory.portal
    if (portal and portal.enabled and not portal.host and not no_portal
            and not host_ids and not no_write):
        try:
            ph = provision_portal(inventory, inventory_path)
            if ph is not None:
                hosts = [*hosts, ph]
        except ConfigError as exc:
            portal_error = str(exc)

    payload = provision_payload(inventory.name, hosts, inventory_path=inventory_path)
    if output_fmt == "json":
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    table = Table(title=f"Fleet provision — {inventory.name}", show_header=True)
    table.add_column("id", style="bold")
    table.add_column("action")
    table.add_column("ip")
    table.add_column("instance")
    table.add_column("expires (UTC)")
    for h in hosts:
        table.add_row(
            h.id,
            h.labels.get("provision_action", "—"),
            h.public_ip,
            h.provider_id or "—",
            h.labels.get("expires_at", "—"),
        )
    console.print()
    console.print(table)
    if not no_write:
        console.print(f"\n  Updated: [cyan]{inventory_path}[/cyan]")
    console.print(
        "  Every host powers off and is terminated at its expiry (dead-man switch). "
        "Longer workshop: set provider.ttl_hours before provisioning."
    )
    console.print("  Next: [bold]rodeo fleet deploy -f …[/bold] then [bold]doctor[/bold]\n")
    if inventory.student_access == "open":
        console.print(
            "  [yellow]student_access: open[/yellow]: lab UI ports stay operator-only "
            "until every lab is up; then run [bold]rodeo fleet open-access -f …[/bold]\n"
        )
    if portal_error:
        console.print(f"  [red]✗  Portal VM: {portal_error}[/red]\n")
        raise SystemExit(1)
    if portal and portal.enabled and not no_portal:
        console.print("  Portal: [bold]rodeo fleet portal up -f …[/bold] then, once labs are "
                      "ready, [bold]rodeo fleet portal publish -f …[/bold]\n")


@fleet_cmd.command("deprovision")
@_output_opt
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Required — refuse to terminate without explicit confirmation.",
)
@click.option(
    "--keep-portal",
    is_flag=True,
    default=False,
    help="Leave the claim portal VM running (e.g. to keep the claim list).",
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
    keep_portal: bool,
) -> None:
    """Terminate ownership-tagged cloud instances for this workshop (F4).

    Also terminates the claim portal VM unless ``--keep-portal`` or ``--host``."""
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
        if not host_ids and not keep_portal:
            results = [*results, *deprovision_portal(inventory, inventory_path)]
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


@fleet_cmd.command("open-access")
@_output_opt
@_concurrency_opt(8)
@click.option(
    "--close",
    is_flag=True,
    default=False,
    help="Remove 0.0.0.0/0 again (back to operator-only).",
)
@click.option(
    "-f",
    "--file",
    "inventory_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to workshop.yaml inventory.",
)
def fleet_open_access_cmd(
    inventory_path: Path,
    close: bool,
    concurrency: int,
    output_fmt: str,
) -> None:
    """Open lab UI ports to the internet for students (needs student_access: open).

    Refuses unless every host has finished all phases and its Harvester /
    Rancher admin passwords are strong. The security group is shared by the
    whole workshop, so this always covers every host. SSH (22) stays
    operator-only; with ``portal.student_ssh`` students get their own sshd on
    2222. A later ``fleet provision`` closes the ports again.
    """
    try:
        inventory = load_inventory(inventory_path)
        result = fleet_open_access(inventory, close=close, concurrency=concurrency)
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    if output_fmt == "json":
        click.echo(json.dumps(open_access_payload(result), indent=2, sort_keys=True))
    elif result.action == "closed":
        console.print(
            f"\n  [green]✓[/green]  {inventory.name}: lab UI ports back to operator-only "
            f"({result.security_group})\n"
        )
    else:
        table = Table(title=f"Fleet open-access: {inventory.name}", show_header=True)
        table.add_column("id", style="bold")
        table.add_column("ready")
        table.add_column("reason", overflow="fold")
        for r in result.hosts:
            table.add_row(
                r.id,
                "[green]yes[/green]" if r.ready else "[red]no[/red]",
                r.reason or "",
            )
        console.print()
        console.print(table)
        ports = ", ".join(str(p) for p in result.ports)
        if result.action == "opened":
            console.print(
                f"\n  [yellow]⚠  Ports {ports} are now open to 0.0.0.0/0 on "
                f"{len(result.hosts)} host(s) ({result.security_group}).[/yellow]\n"
                "  Close with [bold]rodeo fleet open-access --close -f …[/bold]\n"
            )
        else:
            console.print(
                f"\n  [red]✗  Not opened: every host must be ready before ports {ports} "
                "are exposed.[/red]\n"
            )
    if result.action == "refused":
        raise SystemExit(1)


from .fleet_portal_cmd import portal_group  # noqa: E402

fleet_cmd.add_command(portal_group)
