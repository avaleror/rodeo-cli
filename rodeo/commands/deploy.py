"""rodeo deploy — orchestrate the full Harvester + Rancher pipeline."""
from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

from ..config import find_ansible_root, load_config, validate_config
from ..engine.runner import (
    DeployComplete,
    DeployRunner,
    LogLine,
    PhaseDone,
    PhaseFailed,
    PhaseSkipped,
    PhaseStarted,
)
from ..state import PHASES

console = Console()


@click.command("deploy")
@click.option("--config", "config_path", default="rodeo-plan.yaml", show_default=True)
@click.option(
    "--from", "from_phase",
    type=click.Choice(PHASES),
    default=None,
    help="Resume from a specific phase.",
)
@click.option("--ansible-path", default=None, help="Path containing ansible/playbook.yml.")
@click.option("--tui/--no-tui", default=None,
              help="Force TUI on/off (default: auto-detect TTY).")
@click.option("--install-collections", is_flag=True, default=True,
              help="Run ansible-galaxy install before Ansible phases.")
@click.option("--force", is_flag=True, default=False,
              help="Re-run all phases, ignoring phase state.")
def deploy_cmd(
    config_path: str,
    from_phase: str | None,
    ansible_path: str | None,
    tui: bool | None,
    install_collections: bool,
    force: bool,
) -> None:
    """Deploy the full SUSE Virtualization Rodeo cluster."""
    cfg = load_config(config_path)
    try:
        validate_config(cfg)
    except ValueError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    root = Path(ansible_path) if ansible_path else find_ansible_root(cfg)
    if root is None or not (root / "ansible" / "playbook.yml").exists():
        console.print(
            "[red]Cannot find ansible/playbook.yml.[/red]\n"
            "Set [bold]ansible.path[/bold] in rodeo-plan.yaml, "
            "use [bold]--ansible-path[/bold], "
            "or set [bold]RODEO_ANSIBLE_PATH[/bold]."
        )
        raise SystemExit(1)

    use_tui = sys.stdout.isatty() if tui is None else tui
    if use_tui:
        try:
            from ..app import RodeoApp
            app = RodeoApp(
                cfg=cfg,
                ansible_root=root,
                from_phase=from_phase,
                install_collections=install_collections,
                force=force,
            )
            app.run()
            return
        except ImportError:
            console.print("[yellow]⚠  textual not installed — falling back to plain output[/yellow]")

    _deploy_plain(cfg, root, from_phase, install_collections, force)


def _deploy_plain(
    cfg: dict,
    root: Path,
    from_phase: str | None,
    install_collections: bool,
    force: bool = False,
) -> None:
    runner = DeployRunner(
        cfg=cfg,
        root=root,
        from_phase=from_phase,
        install_collections=install_collections,
        force=force,
    )

    console.print(f"\n[bold]rodeo deploy[/bold]  [{root}]\n")

    for event in runner.run():
        if isinstance(event, PhaseStarted):
            console.rule(f"[bold cyan]{event.phase}[/bold cyan]")
        elif isinstance(event, PhaseSkipped):
            label = "already complete" if event.reason == "done" else "skipped"
            console.print(f"  [dim]skip[/dim]  {event.phase}  ({label})")
        elif isinstance(event, LogLine):
            console.print(event.line)
        elif isinstance(event, PhaseDone):
            m, s = divmod(int(event.elapsed), 60)
            console.print(f"  [green]✓[/green]  {event.phase}  [dim]{m}:{s:02d}[/dim]")
        elif isinstance(event, PhaseFailed):
            console.print(f"[red]✗  {event.phase} failed (exit {event.rc})[/red]")
            raise SystemExit(event.rc or 1)
        elif isinstance(event, DeployComplete):
            net = cfg["network"]
            console.print("\n[bold green]✓  Deployment complete.[/bold green]")
            console.print(f"  Harvester:  https://{net['vip']}")
            console.print(f"  Rancher:    https://{net['rancher_ip']}:30002\n")
