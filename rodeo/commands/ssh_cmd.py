"""rodeo ssh <vm> — SSH into a rodeo VM."""
from __future__ import annotations

import os

import click
from rich.console import Console

from ..config import load_config

console = Console()

_VM_NAMES = ["harvester1", "harvester2", "harvester3", "rancher"]
_DEFAULT_KEY = os.path.expanduser("~/.ssh/id_ed25519")


@click.command("ssh")
@click.argument("vm", type=click.Choice(_VM_NAMES))
@click.option("--config", "config_path", default="rodeo-plan.yaml", show_default=True)
@click.option("--key", default=_DEFAULT_KEY, show_default=True, help="SSH private key.")
@click.option("-l", "--login", "login_user", default=None, help="Override SSH user.")
@click.option("-c", "--command", "remote_cmd", default=None, help="Run command and exit.")
def ssh_cmd(
    vm: str,
    config_path: str,
    key: str,
    login_user: str | None,
    remote_cmd: str | None,
) -> None:
    """Open an SSH session to a rodeo VM."""
    cfg = load_config(config_path)
    vm_info = cfg["vms"][vm]
    ip = vm_info["ip"]
    user = login_user or vm_info["user"]

    ssh_args = [
        "ssh",
        "-i", key,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        f"{user}@{ip}",
    ]
    if remote_cmd:
        ssh_args.append(remote_cmd)

    os.execvp("ssh", ssh_args)
