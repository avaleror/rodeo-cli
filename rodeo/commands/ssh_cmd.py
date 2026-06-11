"""rodeo ssh <vm> — SSH into a rodeo VM."""
from __future__ import annotations

import os

import click
from rich.console import Console

console = Console()

_VM_IPS = {
    "harvester1": "192.168.122.11",
    "harvester2": "192.168.122.12",
    "harvester3": "192.168.122.13",
    "rancher":    "192.168.122.9",
}
_VM_USERS = {
    "harvester1": "rancher",
    "harvester2": "rancher",
    "harvester3": "rancher",
    "rancher":    "root",
}
_DEFAULT_KEY = os.path.expanduser("~/.ssh/id_ed25519")


@click.command("ssh")
@click.argument("vm", type=click.Choice(list(_VM_IPS)))
@click.option("--config", "config_path", default="rodeo-plan.yaml", show_default=True)
@click.option("--key", default=_DEFAULT_KEY, show_default=True, help="SSH private key.")
@click.option("-l", "--login", "login_user", default=None, help="Override SSH user.")
@click.option("-c", "--command", "remote_cmd", default=None, help="Run command and exit.")
def ssh_cmd(vm: str, config_path: str, key: str, login_user: str | None, remote_cmd: str | None) -> None:
    """Open an SSH session to a rodeo VM."""
    ip = _VM_IPS[vm]
    user = login_user or _VM_USERS[vm]

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
