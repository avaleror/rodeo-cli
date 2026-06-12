"""rodeo logs — tail a VM serial console log, or build a support bundle."""
from __future__ import annotations

import os
import tarfile
import tempfile
import time
from pathlib import Path

import click
import yaml
from rich.console import Console

from ..config import load_config
from ..state import _state_path

console = Console()

_SERIAL_LOG_DIR = Path("/var/log/libvirt/qemu")
_BUNDLE_TAIL_LINES = 2000


@click.command("logs")
@click.argument("vm", required=False)
@click.option("--config", "config_path", default="rodeo-plan.yaml", show_default=True)
@click.option("--config-dir", "config_dir", default=None, metavar="DIR", type=click.Path(file_okay=False, dir_okay=True, exists=False))
@click.option("-n", "--lines", default=50, show_default=True, help="Initial lines to show.")
@click.option("--no-follow", is_flag=True, help="Print and exit (no -f).")
@click.option("--log-dir", default=str(_SERIAL_LOG_DIR), show_default=True)
@click.option("--bundle", is_flag=True,
              help="Write a support bundle (serial log tails, phase state, redacted plan).")
@click.option("-o", "--output", default=None,
              help="Bundle output path (default: rodeo-bundle-<timestamp>.tar.gz).")
def logs_cmd(
    vm: str | None,
    config_path: str,
    config_dir: str | None,
    lines: int,
    no_follow: bool,
    log_dir: str,
    bundle: bool,
    output: str | None,
) -> None:
    """Tail the serial console log for a VM, or collect a support bundle."""
    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")
    cfg = load_config(config_path, config_dir=config_dir)

    if bundle:
        _make_bundle(cfg, Path(log_dir), output)
        return

    vms = cfg.get("vms", {})
    if vm is None or vm not in vms:
        known = ", ".join(vms) or "none"
        console.print(f"[red]✗  Specify a VM ({known}) or use --bundle.[/red]")
        raise SystemExit(1)

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


def _tail_text(path: Path, max_lines: int) -> str:
    lines = path.read_text(errors="replace").splitlines()
    return "\n".join(lines[-max_lines:]) + "\n"


def _make_bundle(cfg: dict, log_dir: Path, output: str | None) -> None:
    """Collect deploy state, a credential-redacted plan, and serial log tails."""
    from .. import __version__

    plan_name = cfg.get("name", "default")
    dest = Path(output or f"rodeo-bundle-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz")

    redacted = dict(cfg)
    redacted["credentials"] = {k: "REDACTED" for k in cfg.get("credentials", {})}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "rodeo-version.txt").write_text(f"rodeo-cli {__version__}\n")
        (tmp_path / "config-redacted.yaml").write_text(
            yaml.dump(redacted, default_flow_style=False)
        )

        state_file = _state_path(plan_name)
        if state_file.exists():
            (tmp_path / "state.yaml").write_text(state_file.read_text())

        collected = []
        for vm in cfg.get("vms", {}):
            log_file = log_dir / f"{vm}_serial.log"
            if log_file.exists():
                (tmp_path / f"{vm}_serial.tail.log").write_text(
                    _tail_text(log_file, _BUNDLE_TAIL_LINES)
                )
                collected.append(vm)

        with tarfile.open(dest, "w:gz") as tar:
            for f in sorted(tmp_path.iterdir()):
                tar.add(f, arcname=f"rodeo-bundle/{f.name}")

    console.print(f"[green]✓[/green]  [[{dest}]]")
    console.print(
        f"  [dim]plan: {plan_name} · serial logs: "
        f"{', '.join(collected) if collected else 'none found'} · credentials redacted[/dim]"
    )
