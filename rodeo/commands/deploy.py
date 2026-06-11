"""rodeo deploy — run the full Harvester + Rancher deployment pipeline."""
from __future__ import annotations

import subprocess
import sys
import time
import ssl
import urllib.request
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from ..config import load_config, find_ansible_path
from ..state import is_phase_done, mark_phase_done, mark_phase_failed, reset_from

console = Console()

_PHASE_TAGS = {
    "preflight": ["preflight"],
    "kvm_host":  ["kvm_host"],
    "vms":       ["vms"],
    "rancher":   ["rancher"],
}

_VIP_TIMEOUT   = 3600  # seconds to wait for Harvester VIP
_RANCHER_TIMEOUT = 1800


def _run_ansible(ansible_path: Path, tags: list[str], inventory: str, extra_vars: dict | None = None) -> int:
    cmd = [
        "ansible-playbook",
        "-i", str(ansible_path / inventory),
        str(ansible_path / "ansible" / "site.yml"),
        "--tags", ",".join(tags),
    ]
    if extra_vars:
        import json
        cmd += ["--extra-vars", json.dumps(extra_vars)]

    proc = subprocess.run(cmd, cwd=str(ansible_path))
    return proc.returncode


def _wait_for_url(url: str, timeout: int, label: str) -> bool:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    with Progress(
        SpinnerColumn(),
        TextColumn(f"  [bold]{label}[/bold]"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as prog:
        prog.add_task("", total=None)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                urllib.request.urlopen(url, timeout=10, context=ctx)
                return True
            except Exception:
                time.sleep(10)
    return False


def _run_phase(phase: str, ansible_path: Path, cfg: dict, from_phase: int) -> None:
    if is_phase_done(phase) and phase not in list(_PHASE_TAGS.keys())[from_phase:]:
        console.print(f"  [dim]skip[/dim]  {phase} (already done)")
        return

    tags = _PHASE_TAGS[phase]
    console.print(f"\n[bold cyan]Phase: {phase}[/bold cyan]")

    rc = _run_ansible(ansible_path, tags, cfg["ansible"]["inventory"])
    if rc != 0:
        mark_phase_failed(phase, f"ansible exited {rc}")
        console.print(f"[red]✗  Phase {phase} failed (exit {rc}).[/red]")
        console.print("    Fix the error and re-run with [bold]--from {phase}[/bold].")
        raise SystemExit(rc)

    mark_phase_done(phase)
    console.print(f"  [green]✓[/green]  {phase}")


@click.command("deploy")
@click.option("--config", "config_path", default="rodeo-plan.yaml", show_default=True)
@click.option(
    "--from", "from_phase",
    type=click.Choice(["preflight", "kvm_host", "vms", "rancher", "finalise"]),
    default=None,
    help="Resume from a specific phase (skips earlier phases).",
)
@click.option("--ansible-path", default=None, help="Override path to ansible/ directory.")
def deploy_cmd(config_path: str, from_phase: str | None, ansible_path: str | None) -> None:
    """Deploy the full SUSE Virtualization Rodeo cluster."""
    cfg = load_config(config_path)

    # Resolve ansible directory
    ap = Path(ansible_path) if ansible_path else find_ansible_path(cfg)
    if ap is None or not (ap / "ansible" / "site.yml").exists():
        console.print(
            "[red]Cannot find ansible/site.yml.[/red]\n"
            "Set [bold]ansible.path[/bold] in rodeo-plan.yaml, "
            "or use [bold]--ansible-path[/bold], "
            "or set [bold]RODEO_ANSIBLE_PATH[/bold]."
        )
        raise SystemExit(1)

    phases = ["preflight", "kvm_host", "vms", "rancher"]
    start_idx = 0
    if from_phase:
        reset_from(from_phase)
        start_idx = phases.index(from_phase) if from_phase in phases else 0

    vip = cfg["network"]["vip"]
    rancher_ip = cfg["network"]["rancher_ip"]

    console.print(f"\n[bold]rodeo deploy[/bold]  →  {ap}\n")

    for idx, phase in enumerate(phases):
        if idx < start_idx:
            console.print(f"  [dim]skip[/dim]  {phase}")
            continue
        _run_phase(phase, ap, cfg, start_idx)

        # After VMs phase: wait for Harvester VIP
        if phase == "vms":
            console.print(f"\n  Waiting for Harvester VIP ({vip}) — up to {_VIP_TIMEOUT // 60} min...")
            if not _wait_for_url(f"https://{vip}", _VIP_TIMEOUT, f"VIP {vip}"):
                console.print(f"[red]✗  VIP {vip} not reachable after {_VIP_TIMEOUT // 60} min.[/red]")
                raise SystemExit(1)
            console.print(f"  [green]✓[/green]  VIP online: https://{vip}")

    # Finalise: enable libvirt-guests + VM autostart
    console.print("\n[bold cyan]Phase: finalise[/bold cyan]")
    _finalise(cfg)

    console.print("\n[bold green]✓  Deployment complete.[/bold green]")
    console.print(f"  Harvester:  https://{vip}")
    console.print(f"  Rancher:    https://{rancher_ip}:30002\n")


def _finalise(cfg: dict) -> None:
    """Enable libvirt-guests and VM autostart so the instance survives reboots."""
    try:
        from ..engine.libvirt import LibvirtDriver, RODEO_VMS

        with LibvirtDriver(cfg["libvirt"]["uri"]) as lv:
            for vm in RODEO_VMS:
                try:
                    lv.set_autostart(vm, True)
                except Exception:
                    pass
    except Exception as exc:
        console.print(f"[yellow]  ⚠  autostart: {exc}[/yellow]")

    subprocess.run(["systemctl", "enable", "libvirt-guests"], check=False)
    mark_phase_done("finalise")
    console.print("  [green]✓[/green]  finalise  (libvirt-guests enabled, VM autostart set)")
