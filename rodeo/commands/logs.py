"""rodeo logs <vm> — tail the libvirt serial console log."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import click
from rich.console import Console

console = Console()

_SERIAL_LOG_DIR = Path("/var/log/libvirt/qemu")
_VALID_VMS = ["harvester1", "harvester2", "harvester3", "rancher"]


@click.command("logs")
@click.argument("vm", type=click.Choice(_VALID_VMS))
@click.option("-n", "--lines", default=50, show_default=True, help="Initial lines to show.")
@click.option("--no-follow", is_flag=True, help="Print and exit (no -f).")
@click.option("--log-dir", default=str(_SERIAL_LOG_DIR), show_default=True)
def logs_cmd(vm: str, lines: int, no_follow: bool, log_dir: str) -> None:
    """Tail the serial console log for a VM."""
    log_file = Path(log_dir) / f"{vm}_serial.log"

    if not log_file.exists():
        console.print(
            f"[yellow]Serial log not found: {log_file}[/yellow]\n"
            "The VM may not have started yet, or serial logging is disabled."
        )
        raise SystemExit(1)

    tail_args = ["tail", f"-n{lines}"]
    if not no_follow:
        tail_args.append("-f")
    tail_args.append(str(log_file))

    os.execvp("tail", tail_args)
