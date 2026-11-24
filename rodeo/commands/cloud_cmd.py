"""rodeo cloud — what you deployed on a shared cloud account (issue #175).

    rodeo cloud ls                       # your deployments in the region
    rodeo cloud ls --all                 # everyone's, with owners
    rodeo cloud destroy 3f9a1c22         # remove one of yours, from any machine
    rodeo cloud destroy 3f9a1c22 --not-mine   # someone else's: type their owner to confirm
    rodeo cloud ls --provider gcp        # the same on GCP (whole project, every region)

Every resource rodeo launches carries ``rodeo-owner`` (the caller ARN, or
``RODEO_OWNER``) and ``rodeo-deployment`` (8 hex chars per deployment). These
commands read them from the cloud, so they work after the local lab key is gone.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from ..config import ConfigError
from ..providers.aws import AwsHostProvider, REGION_ENV
from ..ssh_key import delete_lab_ssh_key, list_lab_ssh_keys

console = Console()
_REGION_ENVS = (REGION_ENV, "AWS_REGION", "AWS_DEFAULT_REGION")


def _plan_region(config_dir: str | None) -> str:
    base = Path(config_dir).expanduser() if config_dir else Path.cwd()
    for name in ("rodeo-plan.yaml", "workshop.yaml"):
        path = base / name
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue
        provider = data.get("provider") if isinstance(data, dict) else None
        if isinstance(provider, dict) and provider.get("region"):
            return str(provider["region"])
    return ""


def resolve_region(region: str | None, config_dir: str | None = None) -> str:
    """--region, else the plan's provider.region, else the AWS region env vars."""
    if region:
        return region
    found = _plan_region(config_dir)
    if found:
        return found
    for var in _REGION_ENVS:
        if os.environ.get(var, "").strip():
            return os.environ[var].strip()
    raise ConfigError(
        "no AWS region: pass --region, run from a lab with provider.region, "
        f"or set one of {', '.join(_REGION_ENVS)}"
    )


def _short_owner(owner: str) -> str:
    return owner.rsplit("/", 1)[-1] if owner else "?"


def _age(launched: Any) -> str:
    if isinstance(launched, str) and launched:
        try:
            launched = datetime.fromisoformat(launched)
        except ValueError:
            return "-"
    if not isinstance(launched, datetime):
        return "-"
    delta = datetime.now(timezone.utc) - launched.astimezone(timezone.utc)
    hours, rem = divmod(int(delta.total_seconds()) // 60, 60)
    return f"{hours}h{rem:02d}"


def _local_keys() -> dict[str, Any]:
    return {k.key_name: k for k in list_lab_ssh_keys()}


def _is_mine(row: dict[str, Any], owner: str, local: dict[str, Any]) -> bool:
    if row["legacy"]:
        if row.get("ssh_keys"):  # GCP: the lab public key sits in the ssh-keys metadata
            blobs = []
            for key in local.values():
                try:
                    blobs.append(key.public.read_text().split()[1])
                except (OSError, IndexError):
                    continue
            return any(b in row["ssh_keys"] for b in blobs)
        return row["key_name"] in local
    return row["owner"] == owner


def _target(provider: str, region: str | None, project: str | None,
            config_dir: str | None) -> tuple[Any, dict[str, Any], str]:
    """(provider object, its config, where) for --provider aws|gcp."""
    if provider == "gcp":
        from ..providers.gcp import GcpHostProvider, default_project

        cfg = {"type": "gcp", "project": project or default_project()}
        return GcpHostProvider(), cfg, f"GCP project {cfg['project']}"
    cfg = {"region": resolve_region(region, config_dir)}
    return AwsHostProvider(), cfg, cfg["region"]


_PROVIDER_OPT = click.option("--provider", "provider", type=click.Choice(["aws", "gcp"]),
                             default="aws", show_default=True, help="Which cloud.")
_PROJECT_OPT = click.option("--project", default=None,
                            help="GCP project (default: environment / gcloud).")


@click.group("cloud")
def cloud_cmd() -> None:
    """Your rodeo deployments on a shared cloud account (AWS or GCP)."""


@cloud_cmd.command("ls")
@click.option("--mine/--all", "mine", default=True, show_default=True,
              help="Only your deployments, or everyone's.")
@click.option("--region", default=None, help="AWS region (default: plan, then env).")
@click.option("--config-dir", "config_dir", default=None, metavar="DIR",
              help="Lab directory to read provider.region from.")
@_PROVIDER_OPT
@_PROJECT_OPT
def cloud_ls(mine: bool, region: str | None, config_dir: str | None, provider: str,
             project: str | None) -> None:
    """List live rodeo deployments (one AWS region, or a whole GCP project)."""
    prov, cfg, where = _target(provider, region, project, config_dir)
    owner = prov.owner(cfg)
    local = _local_keys()
    rows = prov.list_deployments(cfg)
    if mine:
        rows = [r for r in rows if _is_mine(r, owner, local)]
    if not rows:
        console.print(f"No {'deployments of yours' if mine else 'rodeo deployments'} "
                      f"in {where}.")
        return
    table = Table(title=f"rodeo deployments in {where}")
    for col in ("deployment", "lab", "owner", "hosts", "state", "age", "expires"):
        table.add_column(col, overflow="fold")
    for r in rows:
        states = ",".join(sorted(set(r["states"])))
        table.add_row(
            r["deployment"] or "legacy",
            r["workshop"],
            _short_owner(r["owner"]) if not r["legacy"] else "unknown (legacy)",
            str(len(r["hosts"])),
            states,
            _age(r["launched"]),
            r["expires"] or "-",
        )
    console.print(table)


@cloud_cmd.command("destroy")
@click.argument("deployment")
@click.option("--not-mine", "not_mine", is_flag=True,
              help="Remove a deployment owned by someone else (asks you to type the owner).")
@click.option("--yes", "-y", "assume_yes", is_flag=True,
              help="Skip the confirmation for your own deployment.")
@click.option("--region", default=None, help="AWS region (default: plan, then env).")
@click.option("--config-dir", "config_dir", default=None, metavar="DIR",
              help="Lab directory to read provider.region from.")
@_PROVIDER_OPT
@_PROJECT_OPT
def cloud_destroy(deployment: str, not_mine: bool, assume_yes: bool,
                  region: str | None, config_dir: str | None, provider: str,
                  project: str | None) -> None:
    """Terminate one deployment: instances, security groups or firewall, key pair."""
    deployment = deployment.strip().lower()
    prov, cfg, where = _target(provider, region, project, config_dir)
    owner = prov.owner(cfg)
    rows = [r for r in prov.list_deployments(cfg) if r["deployment"] == deployment]
    owners = sorted({r["owner"] for r in rows})
    foreign = [o for o in owners if o != owner]
    if foreign:
        if not not_mine:
            raise ConfigError(
                f"deployment {deployment} belongs to {', '.join(foreign)}. "
                "Pass --not-mine to remove it anyway."
            )
        typed = click.prompt(
            f"Deployment {deployment} belongs to {foreign[0]}. Type that owner to confirm",
            default="", show_default=False,
        ).strip()
        if not typed or typed != foreign[0]:
            console.print("Aborted: the owner did not match.")
            raise SystemExit(1)
    elif not assume_yes and not Confirm.ask(
        f"Terminate deployment {deployment} in {where}?", default=False,
    ):
        console.print("Aborted.")
        raise SystemExit(0)
    results = prov.destroy_deployment(cfg, deployment, allow_foreign=bool(foreign))
    for r in results:
        console.print(f"[green]✓[/green]  {r.id}  {r.provider_id or '-'}  {r.detail or 'done'}")
    if foreign:
        return
    # Our own deployment: drop the local key and host state that pointed at it.
    from ..providers.remote_up import clear_aws_host_state

    for key in list_lab_ssh_keys():
        if key.deployment == deployment:
            delete_lab_ssh_key(key.workshop)
            clear_aws_host_state(key.workshop)
            console.print(f"  [dim]local:[/dim] removed key and host state for {key.workshop}")
