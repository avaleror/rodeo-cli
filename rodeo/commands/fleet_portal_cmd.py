"""rodeo fleet portal: student claim portal (F5): install, publish, instructor controls."""
from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from ..config import ConfigError
from ..fleet.inventory import load_inventory
from ..fleet.portal import (
    admin_link,
    portal_admin,
    portal_invite,
    portal_publish,
    portal_up,
    portal_url,
    portal_watch,
)

console = Console()


def _file(fn):
    return click.option(
        "-f", "--file", "inventory_path", required=True,
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Path to workshop.yaml inventory.",
    )(fn)


def _json(fn):
    return click.option("--output", "output_fmt", type=click.Choice(["text", "json"]),
                        default="text", show_default=True, help="Output format.")(fn)


def _fail(exc: Exception) -> None:
    console.print(f"[red]✗  {exc}[/red]")
    raise SystemExit(1)


@click.group("portal")
def portal_group() -> None:
    """Student claim portal: students claim their own lab with an event code or invite link."""


@portal_group.command("up")
@click.option("--no-wait", is_flag=True, help="Do not wait for the HTTPS certificate.")
@_file
def portal_up_cmd(inventory_path: Path, no_wait: bool) -> None:
    """Install or update the portal service + Caddy (TLS) on the portal VM. Idempotent."""
    try:
        url = portal_up(load_inventory(inventory_path), wait=not no_wait)
    except ConfigError as exc:
        _fail(exc)
    console.print(f"\n  [green]✓[/green]  Portal up: [bold]{url}[/bold]")
    console.print("  Next: [bold]rodeo fleet portal publish -f …[/bold] once the labs are ready\n")


@portal_group.command("publish")
@click.option("-j", "--concurrency", default=8, show_default=True, type=click.IntRange(1, 64))
@click.option("--watch", is_flag=True,
              help="Keep running during the deploy: push progress to the instructor page and "
                   "publish each lab as soon as it is ready. Stops when all labs are ready.")
@click.option("--interval", default=60, show_default=True, type=click.IntRange(15, 3600),
              help="Seconds between updates with --watch.")
@_json
@_file
def portal_publish_cmd(inventory_path: Path, concurrency: int, output_fmt: str,
                       watch: bool, interval: int) -> None:
    """Push every lab (URLs, credentials, student SSH) to the portal. Safe to re-run.

    Labs that are not ready yet are published as "building" and cannot be claimed.
    With --watch, run it right after `fleet deploy` and follow the deploy on the
    instructor page (`rodeo fleet portal admin-link`).
    """
    if watch:
        _watch(inventory_path, concurrency=concurrency, interval=interval)
        return
    try:
        inventory = load_inventory(inventory_path)
        rows = portal_publish(inventory, concurrency=concurrency)
    except ConfigError as exc:
        _fail(exc)
    if output_fmt == "json":
        click.echo(json.dumps([r.__dict__ for r in rows], indent=2))
        return
    t = Table(title=f"Portal publish: {inventory.name}")
    for col in ("id", "state", "student ssh", "reason"):
        t.add_column(col, overflow="fold")
    for r in rows:
        t.add_row(r.id, "[green]ready[/green]" if r.ready else "[yellow]building[/yellow]",
                  "yes" if r.ssh else "-", r.reason or "")
    console.print()
    console.print(t)
    console.print(f"\n  Students: [bold]{portal_url(inventory)}[/bold]\n")


def _watch(inventory_path: Path, *, concurrency: int, interval: int) -> None:
    import time as _time

    try:
        inventory = load_inventory(inventory_path)
    except ConfigError as exc:
        _fail(exc)

    def tick(t, progress) -> None:
        stamp = _time.strftime("%H:%M")
        busy = ", ".join(
            f"{hid} {progress[hid]['current'] or '?'} {progress[hid]['done']}/{progress[hid]['total']}"
            for hid in t.building
        )
        line = (f"  {stamp}  ready {len(t.ready)}/{len(inventory.hosts)}"
                + (f"  [red]failed: {', '.join(t.failed)}[/red]" if t.failed else "")
                + (f"  [green]published: {', '.join(t.newly_published)}[/green]"
                   if t.newly_published else "")
                + (f"  [dim]{busy}[/dim]" if busy else ""))
        console.print(line)

    console.print(f"\n  Watching {inventory.name} every {interval}s "
                  f"(Ctrl-C to stop). Students: [bold]{portal_url(inventory)}[/bold]\n")
    try:
        portal_watch(inventory, inventory_path, interval=interval, concurrency=concurrency,
                     on_tick=tick)
    except ConfigError as exc:
        _fail(exc)
    except KeyboardInterrupt:
        console.print("\n  Stopped. Re-run with --watch to resume; published labs stay published.\n")
        return
    console.print("\n  [green]✓[/green]  Every lab is ready and published.\n")


@portal_group.command("info")
@_json
@_file
def portal_info_cmd(inventory_path: Path, output_fmt: str) -> None:
    """Portal URL, event code and whether claiming is open (for the slide)."""
    try:
        inventory = load_inventory(inventory_path)
        info = {**portal_admin(inventory, ["info"]), "url": portal_url(inventory)}
    except ConfigError as exc:
        _fail(exc)
    if output_fmt == "json":
        click.echo(json.dumps(info, indent=2))
        return
    console.print(f"\n  URL         [bold]{info['url']}[/bold]")
    if info["mode"] != "roster":
        console.print(f"  Event code  [bold]{info['code']}[/bold]")
    console.print(f"  Mode        {info['mode']}")
    console.print(f"  Claiming    {'[green]open[/green]' if info['open'] else '[red]closed[/red]'}\n")


@portal_group.command("status")
@_json
@_file
def portal_status_cmd(inventory_path: Path, output_fmt: str) -> None:
    """Who has which lab."""
    try:
        inventory = load_inventory(inventory_path)
        st = portal_admin(inventory, ["status"])
    except ConfigError as exc:
        _fail(exc)
    if output_fmt == "json":
        click.echo(json.dumps(st, indent=2))
        return
    t = Table(title=f"Portal claims: {inventory.name}")
    for col in ("lab", "state", "name", "email", "via", "claimed (UTC)", "first opened"):
        t.add_column(col)
    for r in st["labs"]:
        t.add_row(r["lab"], r["state"], r["name"], r["email"], r["source"], r["claimed_at"],
                  r["opened_at"])
    console.print()
    console.print(t)
    console.print()


@portal_group.command("export")
@click.option("-o", "--out", type=click.Path(dir_okay=False, path_type=Path),
              help="Write the CSV here (0600) instead of stdout.")
@_file
def portal_export_cmd(inventory_path: Path, out: Path | None) -> None:
    """Attendance CSV: lab, name, email, via, claimed_at, opened_at."""
    try:
        data = portal_admin(load_inventory(inventory_path), ["export"], raw=True)
    except ConfigError as exc:
        _fail(exc)
    if out is None:
        click.echo(data, nl=False)
        return
    import os

    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(data)
    console.print(f"  [green]✓[/green]  {out} (contains personal data: delete after use)")


@portal_group.command("admin-link")
@_file
def portal_admin_link_cmd(inventory_path: Path) -> None:
    """Print a new read-only instructor web view link (the previous one stops working)."""
    try:
        url = admin_link(load_inventory(inventory_path))
    except ConfigError as exc:
        _fail(exc)
    console.print(f"\n  Instructor view: [bold]{url}[/bold]")
    console.print("  [yellow]Anyone with this link sees every student's name and email. "
                  "Re-run to revoke it.[/yellow]\n")


@portal_group.command("invite")
@click.option("--rotate", is_flag=True, help="Issue new links for students already invited.")
@_file
def portal_invite_cmd(inventory_path: Path, rotate: bool) -> None:
    """Reserve labs for portal.roster and write personal links to <workshop>-invites.csv."""
    try:
        inventory = load_inventory(inventory_path)
        out, results = portal_invite(inventory, inventory_path, rotate=rotate)
    except ConfigError as exc:
        _fail(exc)
    new = sum(1 for r in results if r.get("token"))
    console.print(f"\n  [green]✓[/green]  {len(results)} student(s), {new} new link(s) -> [bold]{out}[/bold]")
    console.print("  [yellow]The CSV holds personal lab links: send each student only their own.[/yellow]\n")


def _simple(name: str, help_: str, args: list[str] | None = None):
    @portal_group.command(name, help=help_)
    @_file
    def _cmd(inventory_path: Path) -> None:
        try:
            info = portal_admin(load_inventory(inventory_path), args or [name])
        except ConfigError as exc:
            _fail(exc)
        state = "open" if info.get("open") else "closed"
        console.print(f"  [green]✓[/green]  claiming {state}; event code {info.get('code')}")

    return _cmd


_simple("open", "Accept new claims.")
_simple("close", "Stop accepting new claims (existing links keep working).")
_simple("rotate-code", "Replace the event code (e.g. after it leaked).")


@portal_group.command("release")
@click.argument("lab")
@_file
def portal_release_cmd(inventory_path: Path, lab: str) -> None:
    """Free LAB: its link stops working and it can be claimed again."""
    try:
        res = portal_admin(load_inventory(inventory_path), ["release", lab])
    except ConfigError as exc:
        _fail(exc)
    console.print(f"  [green]✓[/green]  {lab} " + ("released" if res["released"] else "was not claimed"))


@portal_group.command("revoke")
@click.argument("email")
@_file
def portal_revoke_cmd(inventory_path: Path, email: str) -> None:
    """Remove EMAIL's claim: their link stops working and the lab is free again."""
    try:
        res = portal_admin(load_inventory(inventory_path), ["revoke", email])
    except ConfigError as exc:
        _fail(exc)
    console.print("  [green]✓[/green]  revoked" if res["revoked"] else "  no claim for that email")


@portal_group.command("reassign")
@click.argument("email")
@click.argument("lab")
@_file
def portal_reassign_cmd(inventory_path: Path, email: str, lab: str) -> None:
    """Move EMAIL to free LAB; their personal link keeps working."""
    try:
        portal_admin(load_inventory(inventory_path), ["reassign", email, lab])
    except ConfigError as exc:
        _fail(exc)
    console.print(f"  [green]✓[/green]  moved to {lab}")


@portal_group.command("unlock")
@click.argument("email")
@_file
def portal_unlock_cmd(inventory_path: Path, email: str) -> None:
    """Reset the wrong-PIN lockout for EMAIL."""
    try:
        portal_admin(load_inventory(inventory_path), ["unlock", email])
    except ConfigError as exc:
        _fail(exc)
    console.print("  [green]✓[/green]  unlocked")
