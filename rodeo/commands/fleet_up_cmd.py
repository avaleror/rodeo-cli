"""rodeo fleet up — a whole workshop in one command.

    rodeo fleet up --profile virt-workshop-aws --count 3      # writes workshop.yaml
    rodeo fleet up -f workshop.yaml                            # an existing inventory

Runs the instructor flow in order: provision, deploy, portal up, wait until every
lab is complete, open the UI ports, publish the labs on the portal and print what
the students need. Every step is the existing ``rodeo fleet`` command, so a
failed run is resumed with the same ``rodeo fleet up -f workshop.yaml``.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import yaml

from ..config import ConfigError
from ..fleet.inventory import FleetInventory, load_inventory

LAB_DIR = "/root/rodeo-lab"
POLL_SECONDS = 120

# Flags that describe the workshop; with -f pointing at an existing file they are refused.
_BUILD_FLAGS = ("profile", "count", "name", "region", "instance_type", "instance_tier",
                "subnet_id", "ttl_hours", "title", "guide_url")


def build_inventory(*, profile: str, count: int, name: str | None = None,
                    region: str | None = None, instance_type: str | None = None,
                    instance_tier: str | None = None, subnet_id: str | None = None,
                    ttl_hours: float = 6, portal: bool = True, title: str | None = None,
                    guide_url: str | None = None, default_subnet: Any = None) -> dict[str, Any]:
    """The workshop.yaml mapping for *count* AWS hosts running *profile*.

    The subnet defaults to the region's default VPC public subnet
    (``default_subnet(provider, instance_type=...)``, an AWS call), so the file
    names a concrete subnet and every later ``fleet`` command uses the same one.
    """
    from ..providers.aws import AwsHostProvider, default_region
    from ..providers.instance_catalog import resolve_instance_type

    provider: dict[str, Any] = {
        "type": "aws",
        "count": count,
        "region": region or default_region(),
        "student_access": "open",
        "ttl_hours": ttl_hours,
    }
    if instance_type:
        provider["instance_type"] = instance_type
    else:
        provider["instance_tier"] = instance_tier or "recommended"
    if subnet_id:
        provider["subnet_id"] = subnet_id
    else:
        itype, _ = resolve_instance_type(profile=profile, instance_type=instance_type,
                                         instance_tier=provider.get("instance_tier"))
        finder = default_subnet or AwsHostProvider().default_subnet
        provider["subnet_id"] = finder(provider, instance_type=itype)
    out: dict[str, Any] = {
        "name": name or "{}-{}".format(profile, datetime.now().strftime("%Y%m%d-%H%M")),
        "lab": {"dir": LAB_DIR, "profile": profile, "target": "baremetal",
                "concurrency": min(count, 8)},
        "defaults": {"ssh_user": "ec2-user"},
        "provider": provider,
    }
    if portal:
        out["portal"] = {"enabled": True, "mode": "open", "student_ssh": True,
                         "title": title or "{} workshop".format(profile)}
        if guide_url:
            out["portal"]["guide_url"] = guide_url
    out["hosts"] = []
    return out


def wait_for_labs(inventory: FleetInventory, inventory_path: Path, *, minutes: float,
                  echo: Any = click.echo, sleep: Any = time.sleep,
                  clock: Any = time.monotonic) -> tuple[str, list[str]]:
    """Poll every lab until all are complete: ``("ok", [])``, ``("failed", ids)``
    as soon as one fails, or ``("timeout", ids still building)``."""
    from ..fleet.deploy import apply_status_to_job
    from ..fleet.job import job_path_for, load_job, save_job
    from ..fleet.portal import host_progress
    from ..fleet.status import fleet_status

    job_file = job_path_for(inventory_path)
    deadline = clock() + minutes * 60
    while True:
        statuses = fleet_status(inventory, inventory.hosts, concurrency=8)
        job = None
        if job_file.is_file():
            # Unreachable hosts are still installing rodeo, not failed deploys.
            job = apply_status_to_job(load_job(job_file), [st for st in statuses if st.ok])
            save_job(job, job_file)
        by_id = {st.id: st for st in statuses}
        progress = {h.id: host_progress(by_id[h.id], job.hosts.get(h.id) if job else None)
                    for h in inventory.hosts}
        failed = [hid for hid, p in progress.items() if p["state"] == "failed"]
        building = [hid for hid, p in progress.items() if p["state"] != "ok"]
        echo("  {}  ready {}/{}  {}".format(
            time.strftime("%H:%M"), len(progress) - len(building), len(progress),
            ", ".join("{} {} {}/{}".format(hid, progress[hid]["current"] or "?",
                                           progress[hid]["done"], progress[hid]["total"])
                      for hid in building)))
        if failed:
            return "failed", failed
        if not building:
            return "ok", []
        if clock() >= deadline:
            return "timeout", building
        sleep(POLL_SECONDS)


@click.command("up")
@click.option("-f", "--file", "inventory_path", type=click.Path(dir_okay=False, path_type=Path),
              default=Path("workshop.yaml"), show_default=True,
              help="Inventory to deploy. With --profile it is written first and must not exist yet.")
@click.option("--profile", default=None, help="Lab profile on every host (e.g. virt-workshop-aws).")
@click.option("--count", default=None, type=click.IntRange(1, 200), help="Number of labs (hosts).")
@click.option("--name", default=None, help="Workshop name, tags its AWS resources (default: <profile>-<date>).")
@click.option("--region", default=None, help="AWS region (default: RODEO_AWS_REGION, else eu-north-1).")
@click.option("--instance-type", default=None, help="EC2 instance type (default: the profile's --instance-tier).")
@click.option("--instance-tier", default=None, type=click.Choice(["budget", "recommended", "performance"]),
              help="Size from the profile's catalog when --instance-type is not given (default: recommended).")
@click.option("--subnet-id", default=None, help="Subnet (default: the region's default VPC public subnet).")
@click.option("--ttl-hours", default=None, type=float,
              help="Dead-man switch: every host is terminated after this many hours (default: 6).")
@click.option("--portal/--no-portal", default=True, show_default=True,
              help="Student claim portal (students claim a lab with a workshop code).")
@click.option("--title", default=None, help="Portal title (default: '<profile> workshop').")
@click.option("--guide-url", default=None, help="Workshop guide the portal links to.")
@click.option("-j", "--concurrency", default=None, type=click.IntRange(1, 64),
              help="Parallel deploys (default: lab.concurrency).")
@click.option("--wait-minutes", default=90, show_default=True, type=click.IntRange(1, 1440),
              help="Give up waiting for the labs after this long.")
@click.option("--ref", default=None, metavar="GIT_REF", help="rodeo-cli ref the hosts run (as fleet deploy --ref).")
@click.pass_context
def fleet_up_cmd(ctx: click.Context, inventory_path: Path, profile: str | None, count: int | None,
                 name: str | None, region: str | None, instance_type: str | None,
                 instance_tier: str | None, subnet_id: str | None, ttl_hours: float | None,
                 portal: bool, title: str | None, guide_url: str | None,
                 concurrency: int | None, wait_minutes: int, ref: str | None) -> None:
    """Provision, deploy and open a whole workshop: one command, start to finish."""
    from .fleet_cmd import (console, fleet_access_cmd, fleet_deploy_cmd, fleet_open_access_cmd,
                            fleet_provision_cmd)
    from .fleet_portal_cmd import portal_info_cmd, portal_publish_cmd, portal_up_cmd

    given = {k: v for k, v in ctx.params.items() if k in _BUILD_FLAGS and v is not None}
    if inventory_path.exists():
        if given:
            raise click.UsageError(
                f"{inventory_path} already exists: resume it with `rodeo fleet up -f {inventory_path}`, "
                "or write a new one with -f <new path> (--{} only describe a new workshop)".format(
                    ", --".join(k.replace("_", "-") for k in given)))
    else:
        if not profile or not count:
            raise click.UsageError(f"{inventory_path} does not exist: give --profile and --count "
                                   "to write it, or -f <existing workshop.yaml>")
        try:
            data = build_inventory(profile=profile, count=count, name=name, region=region,
                                   instance_type=instance_type, instance_tier=instance_tier,
                                   subnet_id=subnet_id, ttl_hours=6 if ttl_hours is None else ttl_hours,
                                   portal=portal, title=title, guide_url=guide_url)
        except ConfigError as exc:
            console.print(f"[red]✗  {exc}[/red]")
            raise SystemExit(1)
        inventory_path.parent.mkdir(parents=True, exist_ok=True)
        inventory_path.write_text(yaml.safe_dump(data, sort_keys=False))
        console.print(f"\n  Wrote [cyan]{inventory_path}[/cyan] ({data['name']}, {count} lab(s), "
                      f"{data['provider']['region']})")

    def step(text: str) -> None:
        console.print(f"\n[bold cyan]==> {text}[/bold cyan]")

    try:
        inventory = load_inventory(inventory_path)
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)
    with_portal = bool(inventory.portal and inventory.portal.enabled)
    if inventory.provider is not None:
        step("Provision")
        ctx.invoke(fleet_provision_cmd, inventory_path=inventory_path, host_ids=(), output_fmt="text",
                   no_wait_ssh=False, no_write=False, no_portal=False)
    step("Deploy")
    ctx.invoke(fleet_deploy_cmd, inventory_path=inventory_path, labels=(), host_ids=(),
               concurrency=concurrency, output_fmt="text", force=False, ref=ref)
    if with_portal:
        step("Portal")
        ctx.invoke(portal_up_cmd, inventory_path=inventory_path, no_wait=False)

    step(f"Wait for the labs (up to {wait_minutes} min)")
    inventory = load_inventory(inventory_path)
    outcome, hosts = wait_for_labs(inventory, inventory_path, minutes=wait_minutes)
    if outcome != "ok":
        what = "failed" if outcome == "failed" else f"not ready after {wait_minutes} min"
        console.print(f"\n  [red]✗  {', '.join(hosts)}: {what}[/red]")
        console.print(f"  Look:  [bold]rodeo fleet diagnose -f {inventory_path}[/bold]")
        console.print(f"  Fix:   [bold]rodeo fleet retry -f {inventory_path} --failed-only[/bold]")
        console.print(f"  Then:  [bold]rodeo fleet up -f {inventory_path}[/bold] (finished steps are skipped)\n")
        raise SystemExit(1)

    if inventory.student_access == "open":
        step("Open the lab UIs to students")
        ctx.invoke(fleet_open_access_cmd, inventory_path=inventory_path, close=False,
                   concurrency=8, output_fmt="text")
    if with_portal:
        step("Publish the labs")
        ctx.invoke(portal_publish_cmd, inventory_path=inventory_path, concurrency=8,
                   output_fmt="text", watch=False, interval=60)
        ctx.invoke(portal_info_cmd, inventory_path=inventory_path, output_fmt="text")
    else:
        ctx.invoke(fleet_access_cmd, inventory_path=inventory_path, labels=(), host_ids=(),
                   output_fmt="text")
    console.print(f"  [green]✓[/green]  {inventory.name} is ready.")
    console.print(f"  Teardown: [bold]rodeo fleet deprovision -f {inventory_path} --yes[/bold]\n")
