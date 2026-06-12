"""rodeo deploy — orchestrate the full Harvester + Rancher pipeline."""
from __future__ import annotations

import os
import shutil
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
from ..profiles import get_profile
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
def deploy_cmd(
    config_path: str,
    params: tuple[str, ...],
    paramfile: str | None,
    from_phase: str | None,
    ansible_path: str | None,
    tui: bool | None,
    install_collections: bool,
    force: bool,
    include_guarded: bool,
    preflight_only: bool,
) -> None:
    """Deploy the full SUSE Virtualization Rodeo cluster."""
    try:
        cfg = load_config(config_path, params=params, paramfile=paramfile)
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
        ok = _run_preflight(cfg, root)
        raise SystemExit(0 if ok else 1)

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
                include_guarded=include_guarded,
            )
            app.run()
            raise SystemExit(app.exit_code)
        except ImportError:
            console.print("[yellow]⚠  textual not installed — falling back to plain output[/yellow]")

    _deploy_plain(cfg, root, from_phase, install_collections, force, include_guarded)


def _run_preflight(cfg: dict, root: Path) -> bool:
    """Run preflight checks. Print results. Return True if all pass.
    Core tools (ansible, kubectl) are hard requirements. virsh/ssh are warnings only
    (day-2 commands + fallbacks) since libvirt-python is the primary path.
    """
    res = cfg.get("resources", {})
    storage = cfg.get("storage", {})
    image_dir = Path(storage.get("image_dir", "/var/lib/libvirt/images"))

    checks: list[tuple[str, bool, str]] = []

    # Root
    checks.append(("root", os.geteuid() == 0, "not running as root — some phases require root"))

    # KVM device
    checks.append(("/dev/kvm", Path("/dev/kvm").exists(), "/dev/kvm not found — is KVM enabled?"))

    # Nested virt
    nested = False
    for p in (
        "/sys/module/kvm_intel/parameters/nested",
        "/sys/module/kvm_amd/parameters/nested",
    ):
        try:
            if Path(p).read_text().strip() in ("1", "Y"):
                nested = True
                break
        except OSError:
            pass
    checks.append(("nested virt", nested, "nested virtualization not enabled in kvm module"))

    # RAM
    avail_mib = _read_avail_mib()
    need_mib = (
        res.get("harvester", {}).get("memory_mib", 16384) * 3
        + res.get("rancher", {}).get("memory_mib", 8192)
    )
    if avail_mib > 0:
        ram_ok = avail_mib >= need_mib
        ram_detail = f"need {need_mib // 1024} GB, have {avail_mib // 1024} GB available"
    else:
        ram_ok = True  # can't read — skip
        ram_detail = "could not read /proc/meminfo"
    checks.append(("RAM", ram_ok, ram_detail))

    # Disk
    need_gb = (
        res.get("harvester", {}).get("disk_gb", 270) * 3
        + res.get("rancher", {}).get("disk_gb", 60)
        + 30  # ISOs
    )
    try:
        stat = shutil.disk_usage(str(image_dir))
        free_gb = stat.free // (1024 ** 3)
        disk_ok = free_gb >= need_gb
        disk_detail = f"need ~{need_gb} GB, have {free_gb} GB free in {image_dir}"
    except OSError:
        disk_ok = True
        disk_detail = f"cannot stat {image_dir}"
    checks.append(("disk", disk_ok, disk_detail))

    # Core tools required for deploy phases (ansible + kubectl used by runner/cluster/rancher)
    core_tools = ("ansible-playbook", "ansible-galaxy", "kubectl")
    for tool in core_tools:
        checks.append((
            tool,
            shutil.which(tool) is not None,
            f"{tool} not found in PATH",
        ))

    # Day-2 / convenience tools (attach, ssh, restart, some clean fallbacks use virsh/ssh binaries;
    # libvirt-python path is primary for most ops now). Missing these is a warning only for preflight.
    optional_tools = ("virsh", "ssh")
    for tool in optional_tools:
        checks.append((
            tool,
            shutil.which(tool) is not None,
            f"{tool} not found in PATH (needed for 'attach', 'ssh', and some fallbacks)",
        ))

    console.print(f"\n[bold]Preflight — {cfg.get('name', 'rodeo')}[/bold]\n")
    all_ok = True
    for label, ok, detail in checks:
        if ok:
            console.print(f"  [green]✓[/green]  {label}")
        else:
            if label in optional_tools:
                console.print(f"  [yellow]⚠[/yellow]  {label}  [dim]{detail}[/dim]")
            else:
                console.print(f"  [red]✗[/red]  {label}  [dim]{detail}[/dim]")
                all_ok = False

    console.print()
    if all_ok:
        console.print("[bold green]All checks passed.[/bold green]\n")
    else:
        console.print("[bold red]One or more checks failed.[/bold red]\n")
    return all_ok


def _read_avail_mib() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def _deploy_plain(
    cfg: dict,
    root: Path,
    from_phase: str | None,
    install_collections: bool,
    force: bool = False,
    include_guarded: bool = False,
) -> None:
    runner = DeployRunner(
        cfg=cfg,
        root=root,
        from_phase=from_phase,
        install_collections=install_collections,
        force=force,
        include_guarded=include_guarded,
    )

    console.print(f"\n[bold]rodeo deploy[/bold]  [{root}]\n")

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
            raise SystemExit(event.rc or 1)
        elif isinstance(event, DeployComplete):
            _stop_status()
            net = cfg["network"]
            console.print("\n[bold green]✓  Deployment complete.[/bold green]")
            console.print(f"  Harvester:    https://{net['vip']}")
            console.print(f"  Rancher:      https://{net['rancher_ip']}:30002")
            console.print("  Credentials:  admin / password in ~/.rodeo/secrets.yaml\n")
