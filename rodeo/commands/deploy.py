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
    ProgressUpdate,
)
from ..preflight import run_preflight
from ..profiles import get_profile
from ..success import render_success
from ._options import config_options

console = Console()


@click.command("deploy")
@config_options
@click.option(
    "--from", "from_phase",
    default=None,
    help="Resume from a specific phase (see profile phases).",
)
@click.option("--ansible-path", default=None, help="Path containing ansible/playbook.yml.")
@click.option("--tui/--no-tui", default=None,
              help="Force TUI on/off (default: auto-detect TTY).")
@click.option("--install-collections/--no-install-collections", default=True,
              help="Run ansible-galaxy install before Ansible phases.")
@click.option("--force", is_flag=True, default=False,
              help="Re-run all phases, ignoring phase state.")
@click.option("--finalise", "include_guarded", is_flag=True, default=False,
              help="Run finalise even when deployment_target is 'instruqt' "
                   "(only after the Instruqt image snapshot).")
@click.option("--check", "preflight_only", is_flag=True, default=False,
              help="Run preflight checks and exit without deploying.")
@click.option("--ansible-verbose", "ansible_verbose", default=0, type=int, metavar="LEVEL",
              help="Ansible verbosity level (0-4, like -vvv). Use 3 or 4 + --no-tui for perfect tracing of every ansible task during deployment phases.")
def deploy_cmd(
    config_path: str,
    config_dir: str | None,
    params: tuple[str, ...],
    paramfile: str | None,
    from_phase: str | None,
    ansible_path: str | None,
    tui: bool | None,
    install_collections: bool,
    force: bool,
    include_guarded: bool,
    preflight_only: bool,
    ansible_verbose: int,
) -> None:
    """Deploy the full SUSE Virtualization Rodeo cluster."""
    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")
    try:
        cfg = load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)
        validate_config(cfg)
        profile = get_profile(cfg.get("type", "suse-virt"))
    except ValueError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    if from_phase is not None and from_phase not in profile.phases:
        console.print(
            f"[red]✗  Unknown phase '{from_phase}'. "
            f"Valid phases: {', '.join(profile.phases)}[/red]"
        )
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

    if preflight_only:
        ok = run_preflight(cfg, root)
        raise SystemExit(0 if ok else 1)

    code = execute_deploy(
        cfg, root,
        from_phase=from_phase,
        install_collections=install_collections,
        force=force,
        include_guarded=include_guarded,
        ansible_verbose=ansible_verbose,
        tui=tui,
    )
    raise SystemExit(code)


def execute_deploy(
    cfg: dict,
    root: Path,
    *,
    from_phase: str | None = None,
    install_collections: bool = True,
    force: bool = False,
    include_guarded: bool = False,
    ansible_verbose: int = 0,
    tui: bool | None = None,
) -> int:
    """Run the deploy pipeline (TUI or plain) and return an exit code.

    Shared by ``rodeo deploy`` and ``rodeo up`` so both get identical behavior and
    the same success screen. Prints :func:`render_success` on a clean run (code 0).
    """
    use_tui = sys.stdout.isatty() if tui is None else tui
    code = 0
    if use_tui:
        try:
            from ..app import RodeoApp
            app = RodeoApp(
                cfg=cfg,
                ansible_root=root,
                from_phase=from_phase,
                install_collections=install_collections,
                force=force,
                include_guarded=include_guarded,
                ansible_verbose=ansible_verbose,
            )
            app.run()
            code = app.exit_code
        except ImportError:
            console.print("[yellow]⚠  textual not installed — falling back to plain output[/yellow]")
            code = _deploy_plain(cfg, root, from_phase, install_collections, force,
                                 include_guarded, ansible_verbose)
    else:
        code = _deploy_plain(cfg, root, from_phase, install_collections, force,
                             include_guarded, ansible_verbose)

    if code == 0:
        render_success(cfg)
    return code


def _deploy_plain(
    cfg: dict,
    root: Path,
    from_phase: str | None,
    install_collections: bool,
    force: bool = False,
    include_guarded: bool = False,
    ansible_verbose: int = 0,
) -> int:
    runner = DeployRunner(
        cfg=cfg,
        root=root,
        from_phase=from_phase,
        install_collections=install_collections,
        force=force,
        include_guarded=include_guarded,
        ansible_verbose=ansible_verbose,
    )

    console.print(f"\n[bold]rodeo deploy[/bold]  {root}\n")

    # A live status line absorbs ProgressUpdate events without corrupting
    # log output (the old \r trick smeared lines). Non-TTY (CI) gets the
    # periodic LogLines the poll loops already emit, so nothing is lost.
    status = console.status("") if console.is_terminal else None
    status_started = False

    def _stop_status() -> None:
        nonlocal status_started
        if status is not None and status_started:
            status.stop()
            status_started = False

    for event in runner.run():
        if isinstance(event, PhaseStarted):
            _stop_status()
            console.rule(f"[bold cyan]{event.phase}[/bold cyan]")
        elif isinstance(event, PhaseSkipped):
            labels = {"done": "already complete", "instruqt": "guarded — instruqt target"}
            label = labels.get(event.reason, "skipped")
            console.print(f"  [dim]skip[/dim]  {event.phase}  ({label})")
        elif isinstance(event, ProgressUpdate):
            if status is not None:
                m_e, s_e = divmod(int(event.elapsed), 60)
                m_t = int(event.total) // 60
                detail = f"  {event.detail}" if event.detail else ""
                status.update(f"[cyan]{event.step}[/cyan]{detail}  {m_e}:{s_e:02d} / {m_t}:00")
                if not status_started:
                    status.start()
                    status_started = True
        elif isinstance(event, LogLine):
            console.print(event.line)
        elif isinstance(event, PhaseDone):
            _stop_status()
            m, s = divmod(int(event.elapsed), 60)
            console.print(f"  [green]✓[/green]  {event.phase}  [dim]{m}:{s:02d}[/dim]")
        elif isinstance(event, PhaseFailed):
            _stop_status()
            console.print(f"[red]✗  {event.phase} failed (exit {event.rc})[/red]")
            return event.rc or 1
        elif isinstance(event, DeployComplete):
            _stop_status()

    return 0
