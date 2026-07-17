"""rodeo set-password — rotate the SUSE Virtualization / Rancher Prime admin password.

For an already-deployed lab: generates (or prompts for) a new password, writes it
to ~/.rodeo/secrets.yaml, then applies it live via the Harvester dashboard and/or
Rancher Prime API. No redeploy needed — VMs must already be up and reachable.
"""
from __future__ import annotations

import sys

import click
from rich.console import Console

from ..config import load_config
from ..engine.runner import LogLine, ProgressUpdate
from ..paths import rodeo_secrets_path
from ..privilege import ensure_root, is_root
from ..secretgen import random_password, update_admin_passwords
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


def _has_harvester(cfg: dict) -> bool:
    vms = cfg.get("vms", {})
    harvester_nodes = [n for n in vms if n not in ("rancher", "eib") and not n.startswith("edge")]
    return bool(harvester_nodes)


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


@click.command("set-password")
@config_options
@click.option("--ask", "ask_password", is_flag=True,
              help="Prompt for the new password instead of generating a random one.")
@click.option("--target", type=click.Choice(["both", "harvester", "rancher"]), default="both",
              show_default=True, help="Which admin password(s) to rotate.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def set_password_cmd(
    config_path: str,
    config_dir: str | None,
    params: tuple[str, ...],
    paramfile: str | None,
    ask_password: bool,
    target: str,
    yes: bool,
) -> None:
    """Rotate the Harvester / Rancher Prime admin password on an already-deployed lab.

    \b
    New password source (first match wins):
      1. --ask       interactive hidden prompt
      2. generated    random 16 characters

    Writes the new password to ~/.rodeo/secrets.yaml (harvester_admin_password and
    rancher_admin_password), then logs in with whichever password is currently live
    — the configured one, the last one this tool set, or the admin/admin bootstrap
    default — and changes it. Safe to re-run.
    """
    if not is_root():
        ensure_root(sys.argv[1:])

    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")

    cfg = load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)
    do_rancher = _has_rancher(cfg) and target in ("both", "rancher")
    do_harvester = _has_harvester(cfg) and target in ("both", "harvester")

    if not do_rancher and not do_harvester:
        console.print(
            f"[red]✗  Nothing to do — this profile doesn't have a component matching "
            f"--target {target}.[/red]"
        )
        raise SystemExit(1)

    secrets_path = rodeo_secrets_path()
    if not secrets_path.exists():
        console.print(f"[red]✗  No {secrets_path} found — has this lab been deployed?[/red]")
        raise SystemExit(1)

    if ask_password:
        new_pw = click.prompt(
            "New password (12+ chars, Rancher requires it)",
            hide_input=True, confirmation_prompt=True,
        )
        if len(new_pw) < 12:
            console.print("[red]✗  Password must be at least 12 characters (Rancher minimum).[/red]")
            raise SystemExit(1)
    else:
        new_pw = random_password()

    if not yes:
        labels = [name for name, on in (("Harvester", do_harvester), ("Rancher Prime", do_rancher)) if on]
        console.print(f"\n[bold yellow]Rotate the admin password for: {', '.join(labels)}[/bold yellow]")
        click.confirm("Continue?", abort=True)

    keys = set()
    if do_harvester:
        keys.add("harvester_admin_password")
    if do_rancher:
        keys.add("rancher_admin_password")
    update_admin_passwords(secrets_path, new_pw, keys)
    console.print(f"[dim]New password written to {secrets_path}.[/dim]\n")

    # Re-load so the phase below picks up the password just written.
    cfg = load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)

    from ..engine.rancher import RancherPhase
    phase = RancherPhase(cfg)

    failed = False

    if do_rancher:
        console.print("[bold]Rancher Prime[/bold]")
        if phase.tls_source == "letsEncrypt":
            phase._update_sslip_hostname()
        _drive(phase._configure_api())
        if phase.error:
            console.print(f"[red]✗  Rancher: {phase.error}[/red]\n")
            failed = True
        else:
            console.print("[green]✓  Rancher Prime password updated.[/green]\n")

    if do_harvester:
        console.print("[bold]SUSE Virtualization (Harvester)[/bold]")
        _drive(phase._set_harvester_password())
        if phase.harvester_password_error:
            console.print(f"[red]✗  Harvester: {phase.harvester_password_error}[/red]\n")
            failed = True
        else:
            console.print("[green]✓  Harvester password updated.[/green]\n")

    if failed:
        console.print(
            f"[yellow]New password ({new_pw}) is saved in {secrets_path} but at least "
            "one side wasn't reachable. Once it's back up, either re-run this command "
            "with --ask and paste the same password, or 'rodeo deploy' — it reads "
            "whatever password is already in secrets.yaml and applies it.[/yellow]"
        )
        raise SystemExit(1)

    console.print(f"[bold]New password:[/bold]  {new_pw}")
    console.print(f"[dim](also saved to {secrets_path})[/dim]\n")
