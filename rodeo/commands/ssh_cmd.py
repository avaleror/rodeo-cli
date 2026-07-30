"""rodeo ssh <vm|host|host/vm> — SSH into a rodeo VM or KVM host."""
from __future__ import annotations

import os
from pathlib import Path

import click
from rich.console import Console

from ..config import ConfigError, load_config
from ..ssh_targets import build_ssh_target, ssh_argv_for

console = Console()


def _try_load_config(config_path: str, config_dir: str | None) -> dict:
    """Load plan when present; empty dict on laptop-only host hops."""
    try:
        if config_dir:
            if not (Path(config_dir) / config_path).is_file():
                return {}
        elif not Path(config_path).is_file():
            # Still try load_config — it may resolve via last_lab / config_dir ctx.
            pass
        return load_config(config_path, config_dir=config_dir)
    except Exception:
        return {}


@click.command("ssh")
@click.argument("target")
@click.option("--config", "config_path", default="rodeo-plan.yaml", show_default=True)
@click.option(
    "--config-dir",
    "config_dir",
    default=None,
    metavar="DIR",
    type=click.Path(file_okay=False, dir_okay=True, exists=False),
)
@click.option(
    "--workshop",
    "workshop",
    default=None,
    metavar="FILE",
    type=click.Path(dir_okay=False),
    help="Fleet workshop.yaml for host / host/vm targets (default: ./workshop.yaml).",
)
@click.option(
    "--key",
    default=None,
    help="SSH private key (default: managed ~/.rodeo/ssh/id_ed25519).",
)
@click.option("-l", "--login", "login_user", default=None, help="Override SSH user.")
@click.option("-c", "--command", "remote_cmd", default=None, help="Run command and exit.")
def ssh_cmd(
    target: str,
    config_path: str,
    config_dir: str | None,
    workshop: str | None,
    key: str | None,
    login_user: str | None,
    remote_cmd: str | None,
) -> None:
    """Open an SSH session to a VM, KVM host, or host/vm hop.

    \b
      rodeo ssh rancher              # nested VM (when on the KVM host / local plan)
      rodeo ssh student-01           # provisioned KVM/EC2 host
      rodeo ssh student-01/rancher   # ProxyJump into nested VM via host
    """
    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")
    cfg = _try_load_config(config_path, config_dir)

    try:
        dest = build_ssh_target(
            target,
            cfg=cfg,
            key=key,
            login_user=login_user,
            workshop=workshop,
        )
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1) from exc

    os.execvp("ssh", ssh_argv_for(dest, remote_cmd=remote_cmd))
