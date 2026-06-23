"""rodeo up — the one-command on-ramp for new users.

Front door that ties together the pieces a beginner would otherwise wire by hand:

  check the host  →  install missing deps (with consent)  →  pick a lab that fits
  →  generate secrets silently  →  deploy  →  show how to log in.

Design choices that remove the classic friction:
  - File-based secrets (~/.rodeo/secrets.yaml): no `source rodeo-secrets.env`, no
    `??env:`, no `sudo -E`.
  - Self-escalation: `up` re-execs under sudo for the privileged deploy, so the user
    never types sudo or exports anything.
  - Lab auto-detection: run it inside a lab dir and it just continues; no --config-dir.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import click
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from ..config import find_ansible_root, find_lab_dir, load_config, validate_config
from ..labseed import custom_profile_dir, profile_kind, seed_lab
from ..preflight import (
    PROFILE_SIZING,
    detect_host,
    missing_core_tools,
    profile_label,
    recommend_profile,
    run_preflight,
)
from ..privilege import (
    ensure_root,
    ensure_tmux_session,
    find_rodeo_bin,
    in_tmux,
    is_root,
    sudo_prefix,
    tmux_available,
)
from ..secretgen import ensure_secrets_file
from .deploy import execute_deploy

console = Console()

_VALID_TARGETS = ("baremetal", "instruqt")
DEFAULT_LABS_ROOT = Path.home() / "rodeo-labs"


@click.command("up")
@click.option("--profile", "profile", default=None,
              help="Lab to deploy: 'rancher' (1 VM), 'test' (2-node Harvester), 'harvester-ha' "
                   "(3-node Harvester, no Rancher), or 'harvester' (3-node + Rancher). Or a custom "
                   "profile name. Default: recommended for your RAM.")
@click.option("--name", default=None, help="Lab name (used for the lab directory).")
@click.option("--dir", "lab_dir", default=None, metavar="DIR",
              help="Where to create/use the lab (default: ~/rodeo-labs/<name>).")
@click.option("--yes", "-y", "assume_yes", is_flag=True, help="Accept defaults, no prompts.")
@click.option("--no-deploy", is_flag=True, help="Set up the lab and stop before deploying.")
@click.option("--no-tmux", is_flag=True,
              help="Skip tmux session wrapping (useful inside scripts or existing sessions).")
@click.option(
    "--target",
    "deployment_target",
    default=None,
    type=click.Choice(list(_VALID_TARGETS), case_sensitive=False),
    help="Where this lab runs: 'baremetal' or 'instruqt' (default: auto-detect).",
)
@click.option("--resume", is_flag=True, hidden=True,
              help="Internal: continue after sudo re-exec.")
def up_cmd(profile: str | None, name: str | None, lab_dir: str | None,
           assume_yes: bool, no_deploy: bool, no_tmux: bool,
           deployment_target: str | None, resume: bool) -> None:
    """Bring up a SUSE/Rancher learning lab in one command.

    Runs inside a tmux session automatically so the deploy survives SSH or
    Instruqt disconnects. Re-attach any time with: tmux attach -t rodeo-<profile>
    """
    # --- tmux self-wrap (first thing, before any side effects) ---
    # Skip on Instruqt — the challenge terminal is managed by Instruqt itself.
    # Skip when: already in tmux, --no-tmux, --no-deploy (short-lived), --resume (root re-exec).
    on_instruqt = _detect_target() == "instruqt"
    if not (no_tmux or no_deploy or resume or in_tmux() or on_instruqt):
        session = f"rodeo-{profile or 'up'}"
        if tmux_available():
            ensure_tmux_session(session)  # does not return unless already in tmux
        else:
            console.print(
                "[yellow]⚠  tmux not found — deploy will not survive a disconnect.[/yellow]\n"
                "  Install it:  sudo zypper install -y tmux   (or apt/dnf)\n"
                "  Then re-run: rodeo up\n"
            )

    host = detect_host()

    # Resolve the lab directory (explicit > detected > to-be-created).
    lab: Path | None = None
    if lab_dir:
        lab = Path(lab_dir).expanduser().resolve()
    elif not resume:
        detected = find_lab_dir()
        if detected is not None:
            lab = detected

    if resume:
        # Re-entered as root after escalation: lab + secrets already prepared.
        if lab is None:
            console.print("[red]✗  --resume needs --dir.[/red]")
            raise SystemExit(2)
        _deploy(lab, assume_yes=True)
        return

    # Resolve deployment target: explicit flag > existing plan > auto-detect > prompt.
    # Only prompt when the value came from auto-detection — if the plan or the
    # --target flag already provided it, asking again is noise (and crashes on
    # non-interactive stdin with EOFError).
    _needs_prompt = False
    if deployment_target is None:
        if lab is not None and (lab / "rodeo-plan.yaml").exists():
            import yaml as _yaml
            _plan = _yaml.safe_load((lab / "rodeo-plan.yaml").read_text()) or {}
            _stored = _plan.get("deployment_target")
            if _stored:
                deployment_target = _stored
            else:
                deployment_target = _detect_target()
                _needs_prompt = True
        else:
            deployment_target = _detect_target()
            _needs_prompt = True
        if not assume_yes and _needs_prompt:
            deployment_target = Prompt.ask(
                "Where is this running?",
                choices=list(_VALID_TARGETS),
                default=deployment_target,
            )

    console.print("\n[bold]rodeo up[/bold] — let's get a lab running.\n")
    _print_host(host)

    # 1. Host dependencies.
    if not _ensure_host_ready(host, assume_yes):
        raise SystemExit(1)
    host = detect_host()  # re-read after any install

    # 2. A lab to deploy.
    lab_ready = lab is not None and (
        (lab / "rodeo-plan.yaml").exists() or (lab / "definition.yaml").exists()
    )
    if not lab_ready:
        chosen = profile or _choose_profile(host, assume_yes)
        kind = profile_kind(chosen)
        if kind is None:
            console.print(
                f"[red]✗  No profile named '{chosen}'.[/red]  "
                f"Use rancher / test / harvester, or create your own: [bold]rodeo new {chosen}[/bold]"
            )
            raise SystemExit(1)
        if kind == "custom" and lab is None:
            # Custom profiles are editable and writable — deploy them in place.
            lab = custom_profile_dir(chosen)
            console.print(f"\n[bold]Deploying custom profile '{chosen}'[/bold] from [cyan]{lab}[/cyan]")
        else:
            lab_name = name or (lab.name if lab else chosen)
            if lab is None:
                lab = DEFAULT_LABS_ROOT / lab_name
            console.print(f"\n[bold]Setting up the '{chosen}' lab[/bold] at [cyan]{lab}[/cyan] "
                          f"([dim]{profile_label(chosen)}[/dim])")
            seed_lab(chosen, lab, force=False, deployment_target=deployment_target)
    else:
        console.print(f"\nUsing existing lab at [cyan]{lab}[/cyan].")
        # Persist deployment_target back to the plan so the root re-exec sees
        # the resolved value (e.g. --target flag on a re-run of an existing lab).
        _plan_path = lab / "rodeo-plan.yaml"
        if _plan_path.exists():
            import yaml as _yaml
            _plan_data = _yaml.safe_load(_plan_path.read_text()) or {}
            if _plan_data.get("deployment_target") != deployment_target:
                _plan_data["deployment_target"] = deployment_target
                _plan_path.write_text(_yaml.dump(_plan_data, default_flow_style=False))

    # 3. Secrets — silent, file-based.
    _, _, created = ensure_secrets_file()
    where = "~/.rodeo/secrets.yaml"
    console.print(f"[green]✓[/green]  Secrets {'generated' if created else 'found'} ({where}).")

    if no_deploy:
        console.print("\n[bold]Ready.[/bold] To deploy when you are:")
        console.print(f"  cd {lab}")
        console.print("  rodeo up")
        return

    # 4. Confirm + deploy (escalating to root for the privileged phases).
    if not assume_yes and not Confirm.ask("\nDeploy now? (takes ~1-2 h on nested KVM)", default=True):
        console.print("Nothing deployed. Re-run [bold]rodeo up[/bold] when ready.")
        return

    if not is_root():
        console.print("\n[bold]Switching to root for the install[/bold] (sudo)…")
        ensure_root(["up", "--resume", "--dir", str(lab), "--yes",
                     "--target", deployment_target])  # does not return

    _deploy(lab, assume_yes=assume_yes)


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #

def _print_host(host: dict) -> None:
    t = Table(show_header=False, box=None, pad_edge=False)
    t.add_column(style="dim")
    t.add_column()
    ram = host["ram_total_gib"]
    t.add_row("RAM", f"{ram} GiB total, {host['ram_avail_gib']} GiB available" if ram else "unknown")
    t.add_row("CPUs", str(host["cpus"]) if host["cpus"] else "unknown")
    disk = host["disk_free_gib"]
    t.add_row("Disk", f"{disk} GiB free in {host['image_dir']}" if disk >= 0 else "unknown")
    t.add_row("KVM", "[green]ready[/green]" if host["has_kvm"] else "[red]/dev/kvm missing[/red]")
    t.add_row("Nested virt", "[green]on[/green]" if host["nested"] else "[yellow]off/unknown[/yellow]")
    t.add_row("Package mgr", host["pkg_mgr"])
    console.print(t)


def _ensure_host_ready(host: dict, assume_yes: bool) -> bool:
    """Install missing core deps (with consent). Return True if the host can proceed."""
    missing = missing_core_tools(host)
    needs_install = bool(missing) or not host["has_kvm"]
    if not needs_install:
        return True

    what = ", ".join(missing) if missing else "KVM packages"
    console.print(f"\n[yellow]Missing host dependencies:[/yellow] {what}")
    if not assume_yes and not Confirm.ask("Install them now? (needs sudo)", default=True):
        console.print("Skipped. Install with: [bold]sudo rodeo install-deps[/bold], then re-run.")
        return False

    cmd = sudo_prefix() + [find_rodeo_bin(), "install-deps"]
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        console.print(f"[red]✗  install-deps failed: {exc}[/red]")
        console.print("Fix the host, then run [bold]sudo rodeo install-deps[/bold] manually.")
        return False
    return True


def _detect_target() -> str:
    """Best-effort: are we running on Instruqt?"""
    import os
    if os.environ.get("INSTRUQT_PARTICIPANT_ID") or Path("/etc/instruqt").exists():
        return "instruqt"
    return "baremetal"


def _choose_profile(host: dict, assume_yes: bool) -> str:
    """Pick a profile sized to the host. Recommend, but let the user decide."""
    rec, fits = recommend_profile(host)
    avail = host.get("ram_avail_gib") or host.get("ram_total_gib") or 0

    if assume_yes:
        if not fits:
            console.print(f"[yellow]⚠  No profile fits {avail} GiB RAM; using '{rec}' anyway "
                          f"(it wants more). The deploy may fail.[/yellow]")
        return rec

    console.print("\n[bold]Pick a lab[/bold] (sized for your machine):")
    for tier in PROFILE_SIZING:
        marker = "→" if tier["name"] == rec else " "
        ok = "[green]fits[/green]" if avail >= tier["ram_gib"] else f"[yellow]needs {tier['ram_gib']} GiB[/yellow]"
        console.print(f"  {marker} [bold]{tier['name']}[/bold]  {tier['label']}  ({ok})")

    choice = Prompt.ask("Which lab?", choices=[t["name"] for t in PROFILE_SIZING], default=rec)
    tier = next(t for t in PROFILE_SIZING if t["name"] == choice)
    if avail and avail < tier["ram_gib"]:
        if not Confirm.ask(
            f"[yellow]'{choice}' wants ~{tier['ram_gib']} GiB, you have {avail}. Continue?[/yellow]",
            default=False,
        ):
            raise SystemExit(0)
    return choice


def _deploy(lab: Path, assume_yes: bool) -> None:
    """Load the lab, preflight, and run the pipeline (called as root)."""
    try:
        cfg = load_config("rodeo-plan.yaml", config_dir=str(lab))
        validate_config(cfg)
    except ValueError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    root = find_ansible_root(cfg)
    if root is None or not (root / "ansible" / "playbook.yml").exists():
        console.print("[red]✗  Cannot find the bundled Ansible content. Reinstall rodeo-cli.[/red]")
        raise SystemExit(1)

    if not run_preflight(cfg, root):
        if assume_yes:
            console.print("[red]✗  Preflight failed. Fix the host and re-run.[/red]")
            raise SystemExit(1)
        if not Confirm.ask("Preflight reported problems. Deploy anyway?", default=False):
            raise SystemExit(1)

    code = execute_deploy(cfg, root)
    raise SystemExit(code)
