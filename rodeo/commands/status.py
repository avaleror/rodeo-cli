"""rodeo status — one-shot cluster health table."""
from __future__ import annotations

import json

import click
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..config import load_config
from ..profiles import get_profile
from ..service.status import status_report
from ._options import config_options

console = Console()

_VM_COLOR = {
    "running": "green",
    "shut off": "red",
    "not found": "dim",
    "shutting down": "yellow",
    "paused": "yellow",
    "crashed": "bold red",
    "blocked": "yellow",
    "suspended": "yellow",
}


def _colored(label: str, default: str = "white") -> Text:
    return Text(label, style=_VM_COLOR.get(label, default))


@click.command("status")
@config_options
@click.option(
    "--output",
    "output_fmt",
    type=click.Choice(["text", "json"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--cloud",
    is_flag=True,
    help="Instead: the state of the last remote rodeo up on this lab's host — the AWS host "
         "it provisioned, or the KVM host of deployment_target: remote — (running, exited "
         "with its code, gone, none) and the tail of its log.",
)
@click.option("--lines", type=int, default=40, show_default=True,
              help="With --cloud: log lines to show (at most 500).")
def status_cmd(
    config_path: str,
    config_dir: str | None,
    params: tuple[str, ...],
    paramfile: str | None,
    output_fmt: str,
    cloud: bool,
    lines: int,
) -> None:
    """Show VM states and cluster reachability at a glance."""
    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")
    cfg = load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)
    if cloud:
        _cloud_status(cfg, lines, output_fmt)
        return
    report = status_report(cfg)

    if output_fmt == "json":
        click.echo(json.dumps(report, indent=2, sort_keys=True))
        return

    profile = get_profile(cfg.get("type", "suse-virt"))
    vip = report["vip"]

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0, 1))
    table.add_column("VM", style="bold", min_width=12)
    table.add_column("State", min_width=10)
    table.add_column("Autostart", justify="center")

    if report.get("libvirt_error"):
        console.print(f"[yellow]⚠  {report['libvirt_error']}[/yellow]\n")

    for vm in report["vms"]:
        table.add_row(
            vm["name"],
            _colored(vm["state"]),
            "[green]✓[/green]" if vm["autostart"] else "[dim]—[/dim]",
        )

    console.print()
    console.print("[bold]  VMs[/bold]")
    console.print(table)

    vip_ok = report["vip_reachable"]
    vip_style = "green" if vip_ok else "red"
    vip_label = "reachable" if vip_ok else "unreachable"
    console.print(
        f"\n  [bold]Cluster VIP[/bold]  "
        f"[{vip_style}]{vip} — {vip_label}[/{vip_style}]\n"
    )

    if report.get("phases"):
        console.print("  [bold]Phases[/bold]")
        for phase in profile.phases:
            info = report["phases"].get(phase, {})
            if info.get("completed"):
                icon = "[green]✓[/green]"
                ts = str(info.get("timestamp", ""))[:19].replace("T", " ")
                console.print(f"    {icon}  {phase:<12}  [dim]{ts}[/dim]")
            elif info.get("last_error"):
                console.print(f"    [red]✗[/red]  {phase}")
            else:
                console.print(f"    [dim]○[/dim]  {phase}")
        console.print()


def _cloud_status(cfg: dict, lines: int, output_fmt: str) -> None:
    """``rodeo status --cloud``: one SSH round trip, no job system.

    Reads whichever remote this plan deploys to: the provisioned AWS host, or the KVM host
    of ``deployment_target: remote``, each with its own log, pid and exit files.
    """
    from ..config import ConfigError
    from ..providers.remote_host import deploys_to_remote
    from ..providers.remote_host import status as remote_host_status
    from ..providers.remote_up import remote_up_status

    read = remote_host_status if deploys_to_remote(cfg) else remote_up_status
    try:
        report = read(cfg, lines=lines)
    except ConfigError as exc:
        if output_fmt == "json":
            click.echo(json.dumps({"state": "unknown", "error": str(exc)}, sort_keys=True))
        else:
            console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)
    if output_fmt == "json":
        click.echo(json.dumps(report, indent=2, sort_keys=True))
        return
    state = report["state"]
    detail = {
        "running": f"running (pid {report['pid']})",
        "exited": f"finished, exit {report['exit']}",
        "gone": f"not running and no exit code (pid {report['pid']} is gone: killed or host rebooted)",
        "none": "no deploy recorded",
    }.get(state, state)
    console.print(f"rodeo up on [cyan]{report['ip']}[/cyan]: [bold]{detail}[/bold]  ({report['log']})")
    for line in report["lines"]:
        console.print(line, markup=False, highlight=False)
