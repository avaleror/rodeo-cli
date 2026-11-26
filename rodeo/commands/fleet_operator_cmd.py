"""rodeo fleet operator: run fleet commands from a small host in the cloud."""
from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from ..config import ConfigError
from ..fleet import operator as op
from ..fleet.inventory import load_inventory

console = Console()


def _file(fn):
    return click.option(
        "-f", "--file", "inventory_path", required=True,
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Path to workshop.yaml inventory.",
    )(fn)


def _fail(exc: Exception) -> None:
    console.print(f"[red]✗  {exc}[/red]")
    raise SystemExit(1)


@click.group("operator")
def operator_group() -> None:
    """Run fleet commands from a host in the cloud, so the laptop can sleep.

    \b
    up     create the host, install rodeo and the AWS CLI
    login  `aws login --remote` on the host (once per console session)
    push   workshop dir, lab SSH key, known_hosts, event cert -> host
    run    start `rodeo fleet <command>` there:  run -f ws.yaml -- up --track-ref <sha>
    logs   follow it; status shows how it ended
    pull   bring the results back (the local copy is backed up first)
    down   remove the host and its security group
    """


@operator_group.command("up")
@click.option("--instance-type", default=None, help="Default: t3.small.")
@_file
def up_cmd(inventory_path: Path, instance_type: str | None) -> None:
    """Create or reuse the operator host and install rodeo and the AWS CLI."""
    try:
        inv = load_inventory(inventory_path)
        host = op.provision(inv, instance_type=instance_type)
        console.print(f"  [green]✓[/green]  Operator host {host.public_ip} ({host.provider_id}), "
                      "SSH open to this machine only")
        versions = op.bootstrap(inv, host)
    except ConfigError as exc:
        _fail(exc)
    for line in versions.splitlines()[-2:]:
        console.print(f"     {line}")
    console.print("  Next: [bold]rodeo fleet operator login -f …[/bold], then [bold]push[/bold] and [bold]run[/bold]")


@operator_group.command("login")
@_file
def login_cmd(inventory_path: Path) -> None:
    """Sign the operator host in to AWS: open the URL on any device, paste the code back."""
    try:
        rc = op.login(load_inventory(inventory_path))
    except ConfigError as exc:
        _fail(exc)
    if rc != 0:
        _fail(ConfigError(f"aws login on the operator host failed (exit {rc})"))
    console.print("  [green]✓[/green]  The operator host has its own AWS session; the laptop can sleep.")


@operator_group.command("push")
@_file
def push_cmd(inventory_path: Path) -> None:
    """Copy the workshop dir and the fleet state it needs to the operator host."""
    try:
        size = op.push(load_inventory(inventory_path), inventory_path)
    except ConfigError as exc:
        _fail(exc)
    console.print(f"  [green]✓[/green]  Pushed {size // 1024} KiB")


@operator_group.command("run", context_settings={"ignore_unknown_options": True,
                                                 "allow_interspersed_args": False})
@_file
@click.argument("args", nargs=-1, type=click.UNPROCESSED, required=True)
def run_cmd(inventory_path: Path, args: tuple[str, ...]) -> None:
    """Start `rodeo fleet ARGS` on the operator host and return.

    Push first. Pass any confirmation flag the command needs (--yes): there
    is nobody to answer a prompt there."""
    try:
        op.run(load_inventory(inventory_path), inventory_path, args)
    except ConfigError as exc:
        _fail(exc)
    console.print(f"  [green]✓[/green]  Started on the operator host: rodeo fleet {' '.join(args)}")
    console.print("  Follow it: [bold]rodeo fleet operator logs -f …[/bold] (Ctrl-C stops following, not the run)")


@operator_group.command("logs")
@click.option("--no-follow", is_flag=True, help="Print the last lines and exit.")
@click.option("-n", "--lines", default=200, show_default=True)
@_file
def logs_cmd(inventory_path: Path, no_follow: bool, lines: int) -> None:
    """Output of the run on the operator host."""
    try:
        op.logs(load_inventory(inventory_path), follow=not no_follow, lines=lines)
    except ConfigError as exc:
        _fail(exc)


@operator_group.command("status")
@_file
def status_cmd(inventory_path: Path) -> None:
    """Is the run still going, and how did it end?"""
    try:
        state = op.status(load_inventory(inventory_path))
    except ConfigError as exc:
        _fail(exc)
    console.print(f"  {state}")
    if state.startswith("failed"):
        raise SystemExit(1)


@operator_group.command("pull")
@_file
def pull_cmd(inventory_path: Path) -> None:
    """Bring workshop.yaml, the job file, keys and certs back from the operator host."""
    try:
        inv = load_inventory(inventory_path)
        if op.status(inv) == "running":
            raise ConfigError("the run is still going; pull when `fleet operator status` says done")
        written = op.pull(inv, inventory_path)
    except ConfigError as exc:
        _fail(exc)
    console.print(f"  [green]✓[/green]  {len([w for w in written if not w.startswith('(')])} files back")
    for w in written:
        if w.startswith("("):
            console.print(f"     {w}")


@operator_group.command("down")
@click.option("--yes", is_flag=True, help="Do not ask.")
@_file
def down_cmd(inventory_path: Path, yes: bool) -> None:
    """Terminate the operator host and delete its security group. Pull first."""
    if not yes:
        click.confirm("Terminate the operator host? Anything not pulled is lost", abort=True)
    try:
        results = op.deprovision(load_inventory(inventory_path))
    except ConfigError as exc:
        _fail(exc)
    for r in results:
        mark = "[green]✓[/green]" if r.ok else "[yellow]![/yellow]"
        console.print(f"  {mark}  {r.id}: {r.detail}")
