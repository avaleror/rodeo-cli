"""rodeo install-extensions — reconcile Rancher UI extensions after deployment.

For an already-deployed lab: installs/upgrades whatever rancher.ui_extensions
the plan/definition declares (e.g. the SUSE Virtualization / Harvester
extension) against the running Rancher Prime instance. No redeploy needed —
Rancher must already be up and reachable.
"""
from __future__ import annotations

import sys

import click
from rich.console import Console

from ..config import load_config
from ..engine.runner import LogLine, ProgressUpdate
from ..privilege import ensure_root, is_root
from ._options import config_options

console = Console()


def _has_rancher(cfg: dict) -> bool:
    vms = cfg.get("vms", {})
    if "rancher" in vms:
        return True
    for comp in cfg.get("components", []):
        if isinstance(comp, dict) and comp.get("name") == "rancher":
            return True
    return False


def _drive(events) -> None:
    """Consume a stream_* generator, printing LogLines and a live status for ProgressUpdate."""
    status = console.status("") if console.is_terminal else None
    started = False
    try:
        for event in events:
            if isinstance(event, ProgressUpdate):
                if status is not None:
                    m_e, s_e = divmod(int(event.elapsed), 60)
                    m_t = int(event.total) // 60
                    status.update(f"[cyan]{event.step}[/cyan]  {m_e}:{s_e:02d} / {m_t}:00")
                    if not started:
                        status.start()
                        started = True
            elif isinstance(event, LogLine):
                if status is not None and started:
                    status.stop()
                    started = False
                console.print(event.line, markup=False)
    finally:
        if status is not None and started:
            status.stop()


@click.command("install-extensions")
@config_options
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def install_extensions_cmd(
    config_path: str,
    config_dir: str | None,
    params: tuple[str, ...],
    paramfile: str | None,
    yes: bool,
) -> None:
    """Install/upgrade the Rancher UI extensions declared in rancher.ui_extensions.

    Reconciles each entry declared in the plan's definition (rancher.ui_extensions
    — e.g. the SUSE Virtualization/Harvester extension, pinned to a version)
    against the running Rancher Prime: ensures the ClusterRepo exists, re-indexes
    it, then installs or upgrades the chart in place. Idempotent — already-current
    extensions are left alone. To install something not in the definition, pass
    a --paramfile with a rancher_ui_extensions list, or edit definition.yaml and
    redeploy (which reconciles the same way).
    """
    if not is_root():
        ensure_root(sys.argv[1:])

    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")

    cfg = load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)

    if not _has_rancher(cfg):
        console.print("[red]✗  This profile has no Rancher Prime component — nothing to reconcile.[/red]")
        raise SystemExit(1)

    extensions = cfg.get("rancher_ui_extensions") or []
    if not extensions:
        console.print(
            "[red]✗  No rancher_ui_extensions declared for this plan.[/red]\n"
            "Add one under 'rancher: ui_extensions:' in definition.yaml, or pass "
            "--paramfile with a rancher_ui_extensions list."
        )
        raise SystemExit(1)

    if not yes:
        console.print("\n[bold yellow]Reconcile these Rancher UI extensions:[/bold yellow]")
        for ext in extensions:
            console.print(f"  {ext.get('name')} -> {ext.get('version')}")
        click.confirm("Continue?", abort=True)

    from ..engine.rancher import RancherPhase
    phase = RancherPhase(cfg)
    if phase.tls_source == "letsEncrypt":
        phase._update_sslip_hostname()

    console.print("\n[bold]Authenticating to Rancher Prime[/bold]")
    _drive(phase._configure_api())
    if phase.error:
        console.print(f"[red]✗  Rancher: {phase.error}[/red]")
        raise SystemExit(1)

    console.print("\n[bold]Reconciling UI extensions[/bold]")
    _drive(phase._reconcile_ui_extensions())
    if phase.error:
        console.print(f"[red]✗  {phase.error}[/red]")
        raise SystemExit(1)

    console.print("\n[green]✓  Extensions reconciled.[/green]  Check the Rancher Extensions page for details.\n")
