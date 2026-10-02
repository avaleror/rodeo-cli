"""rodeo instances — several copies of one lab on this host.

    rodeo instances new smlm-workshop --count 3     # seeds ~/rodeo-labs/smlm-workshop-1..3
    rodeo instances up                              # deploys them, one after the other
    rodeo instances list                            # instance, subnet, host ports, deployed?
    rodeo instances clean smlm-workshop-2 --yes     # removes one (or all, without names)

Each lab gets `instance: N` in its plan (see rodeo/instances.py): its own libvirt
network, subnet, DNS domain, host ports and generated passwords. Deploy each with
`rodeo up --dir <lab>` (or `cd <lab> && rodeo up`). Only lab-in-a-box labs support
instances.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

from ..config import ConfigError, load_config
from ..instances import MAX_INSTANCE
from ..labseed import resolve_profile_source, seed_lab
from ..paths import invoking_home

console = Console()


def _labs_root(base: str | None) -> Path:
    return Path(base).expanduser() if base else invoking_home() / "rodeo-labs"


@click.group("instances")
def instances_cmd() -> None:
    """Several instances of one lab on this host (lab-in-a-box labs)."""


@instances_cmd.command("new")
@click.argument("profile")
@click.option("--count", type=click.IntRange(1, MAX_INSTANCE), required=True, help="How many instances.")
@click.option("--start", type=click.IntRange(1, MAX_INSTANCE), default=1, show_default=True,
              help="First instance number (instance 0 is the plain, single lab).")
@click.option("--dir", "base", default=None, metavar="DIR", help="Where to create the labs (default: ~/rodeo-labs).")
def new_instances(profile: str, count: int, start: int, base: str | None) -> None:
    """Seed COUNT labs of PROFILE, numbered from START."""
    plan = yaml.safe_load((resolve_profile_source(profile) / "rodeo-plan.yaml").read_text()) or {}
    if plan.get("type") != "lab-in-a-box":
        raise click.ClickException(f"'{profile}' is type {plan.get('type')!r}: only lab-in-a-box labs support instances")
    last = start + count - 1
    if last > MAX_INSTANCE:
        raise click.ClickException(f"instances {start}..{last}: the highest instance number is {MAX_INSTANCE}")
    root = _labs_root(base)
    for n in range(start, last + 1):
        lab = root / f"{profile}-{n}"
        if (lab / "rodeo-plan.yaml").exists():
            console.print(f"  [yellow]keep[/yellow]  {lab} (already exists)")
            continue
        seed_lab(profile, lab)
        plan_path = lab / "rodeo-plan.yaml"
        data = yaml.safe_load(plan_path.read_text()) or {}
        data["instance"] = n
        plan_path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))
        console.print(f"  [green]✓[/green]  {lab} (instance {n})")
    console.print(f"\nDeploy each with: [bold]rodeo up --dir {root}/{profile}-<n>[/bold]")


@instances_cmd.command("list")
@click.option("--dir", "base", default=None, metavar="DIR", help="Where the labs are (default: ~/rodeo-labs).")
def list_instances(base: str | None) -> None:
    """Show the lab-in-a-box labs under DIR with their instance settings."""
    from ..inventory import build_inventory
    from ..state import is_phase_done

    table = Table(show_header=True, header_style="bold cyan")
    for col in ("Lab", "Instance", "Network", "Subnet", "Host ports", "Deployed"):
        table.add_column(col)
    root = _labs_root(base)
    rows = 0
    for plan_path in sorted(root.glob("*/rodeo-plan.yaml")):
        try:
            cfg = load_config(plan_path, config_dir=plan_path.parent)
            if cfg.get("type") != "lab-in-a-box":
                continue
            inv = build_inventory(cfg)
        except (ConfigError, ValueError) as exc:
            table.add_row(plan_path.parent.name, "?", "", "", "", f"[red]{exc}[/red]")
            continue
        net = inv.get("libvirt_network", {})
        ports = ", ".join(
            f"{s['host_port']}→{s['target']}:{s['guest_port']}"
            for s in (inv.get("_raw_topology") or {}).get("exposed_services") or []
        )
        deployed = is_phase_done("labinabox", cfg.get("name", ""))
        table.add_row(plan_path.parent.name, str(cfg.get("instance", 0)), net.get("name", ""),
                      str(net.get("cidr", "")), ports or "-", "yes" if deployed else "no")
        rows += 1
    if rows:
        console.print(table)
        _print_room(root)
    else:
        console.print(f"No lab-in-a-box labs under {root}.")


def _instance_labs(base: str | None, names: tuple[str, ...]) -> list[Path]:
    """Lab dirs of lab-in-a-box instances (instance >= 1) under DIR, or the named ones."""
    root = _labs_root(base)
    labs = []
    for plan_path in sorted(root.glob("*/rodeo-plan.yaml")):
        data = yaml.safe_load(plan_path.read_text()) or {}
        if data.get("type") == "lab-in-a-box" and data.get("instance"):
            labs.append(plan_path.parent)
    if names:
        known = {lab.name: lab for lab in labs}
        missing = [n for n in names if n not in known]
        if missing:
            raise click.ClickException(f"no instance lab named {', '.join(missing)} under {root}")
        labs = [known[n] for n in names]
    return labs


def _print_room(root: Path) -> None:
    """How many more instances of the first lab would fit in this host's free RAM/disk."""
    from ..preflight import _free_gib, _read_avail_mib, _resource_needs

    first = next(iter(_instance_labs(str(root), ())), None)
    if first is None:
        return
    cfg = load_config(first / "rodeo-plan.yaml", config_dir=first)
    need_mib, need_gb = _resource_needs(cfg)
    free_mib = _read_avail_mib()
    free_gb = _free_gib(cfg.get("storage", {}).get("image_dir", "/var/lib/libvirt/images"))
    if need_mib <= 0 or need_gb <= 0 or free_gb < 0:
        return
    room = min(free_mib // need_mib, free_gb // need_gb)
    console.print(f"\nRoom for about [bold]{room}[/bold] more instance(s) of {first.name.rsplit('-', 1)[0]} "
                  f"(free: {free_mib // 1024} GiB RAM, {free_gb} GB disk; each needs "
                  f"{need_mib // 1024} GiB, {need_gb} GB)")


@instances_cmd.command("up")
@click.argument("names", nargs=-1)
@click.option("--dir", "base", default=None, metavar="DIR", help="Where the labs are (default: ~/rodeo-labs).")
@click.option("--keep-going", is_flag=True, help="Continue with the next lab when one fails.")
def up_instances(names: tuple[str, ...], base: str | None, keep_going: bool) -> None:
    """Deploy instance labs (all under DIR, or NAMES), one after the other."""
    from ..privilege import find_rodeo_bin
    from .up_cmd import _ensure_plan_secrets

    labs = _instance_labs(base, names)
    if not labs:
        raise click.ClickException("no instance labs found — create them with `rodeo instances new`")
    # Ask for any operator secret once, up front, instead of failing N deploys later.
    for lab in labs:
        _ensure_plan_secrets(lab / "rodeo-plan.yaml", assume_yes=False)
    failed = []
    for lab in labs:
        console.print(f"\n[bold]Deploying {lab.name}[/bold]")
        r = subprocess.run([find_rodeo_bin(), "up", "--yes", "--no-tmux", "--dir", str(lab)])
        if r.returncode != 0:
            failed.append(lab.name)
            if not keep_going:
                break
    if failed:
        raise click.ClickException(f"deploy failed for: {', '.join(failed)}")
    console.print(f"\n[green]✓[/green]  {len(labs)} instance(s) deployed")


@instances_cmd.command("clean")
@click.argument("names", nargs=-1)
@click.option("--dir", "base", default=None, metavar="DIR", help="Where the labs are (default: ~/rodeo-labs).")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def clean_instances(names: tuple[str, ...], base: str | None, yes: bool) -> None:
    """Destroy instance labs (all under DIR, or NAMES) — their VMs, network, ports."""
    from ..privilege import find_rodeo_bin

    labs = _instance_labs(base, names)
    if not labs:
        console.print("No instance labs to clean.")
        return
    if not yes:
        click.confirm(f"Destroy {len(labs)} instance lab(s): {', '.join(lab.name for lab in labs)}?", abort=True)
    for lab in labs:
        console.print(f"\n[bold]Cleaning {lab.name}[/bold]")
        subprocess.run([find_rodeo_bin(), "clean", "--yes", "--config-dir", str(lab),
                        "--config", str(lab / "rodeo-plan.yaml")])
