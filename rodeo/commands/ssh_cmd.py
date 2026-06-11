"""rodeo ssh <vm> — SSH into a rodeo VM."""
from __future__ import annotations

import os

import click
from rich.console import Console

from ..config import load_config

console = Console()

@click.command("ssh")
@click.argument("vm")
@click.option("--config", "config_path", default="rodeo-plan.yaml", show_default=True)
@click.option("--key", default=None,
              help="SSH private key (default: ssh.identity_file from plan).")
@click.option("-l", "--login", "login_user", default=None, help="Override SSH user.")
@click.option("-c", "--command", "remote_cmd", default=None, help="Run command and exit.")
def ssh_cmd(
    vm: str,
    config_path: str,
    key: str | None,
    login_user: str | None,
    remote_cmd: str | None,
) -> None:
    """Open an SSH session to a rodeo VM."""
    cfg = load_config(config_path)
    vms = cfg.get("vms", {})
    if vm not in vms:
        console.print(f"[red]✗  Unknown VM '{vm}'. Known: {', '.join(vms)}[/red]")
        raise SystemExit(1)
    vm_info = vms[vm]
    ip = vm_info["ip"]
    user = login_user or vm_info["user"]
    if key is None:
        key = cfg.get("ssh", {}).get(
            "identity_file", os.path.expanduser("~/.ssh/id_ed25519")
        )

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
