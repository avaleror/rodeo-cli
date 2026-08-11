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

from ..config import ConfigError, find_ansible_root, find_lab_dir, load_config, validate_config
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
from ..paths import invoking_home
from ..providers.remote_up import execute_aws_up, on_ec2
from ..secretgen import ensure_secrets_file
from .deploy import execute_deploy

console = Console()

_VALID_TARGETS = ("baremetal", "instruqt", "aws")


def _default_labs_root() -> Path:
    return invoking_home() / "rodeo-labs"


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
    help="Where this lab runs: 'baremetal', 'instruqt', or 'aws' "
         "(aws = provision EC2 then remote deploy; default: auto-detect).",
)
@click.option("--resume", is_flag=True, hidden=True,
              help="Internal: continue after sudo re-exec.")
@click.option(
    "--reconcile/--no-reconcile",
    default=True,
    help="Pass through to deploy: re-run vms when VM memory/vCPU drifts "
         "(default on). Use --no-reconcile to skip drift checks.",
)
@click.option(
    "--instance-tier",
    "instance_tier",
    default=None,
    type=click.Choice(["budget", "recommended", "performance"], case_sensitive=False),
    help="AWS host size tier when --target aws (ignored if provider.instance_type is set). "
         "With --yes and no type/tier, defaults to recommended.",
)
def up_cmd(profile: str | None, name: str | None, lab_dir: str | None,
           assume_yes: bool, no_deploy: bool, no_tmux: bool,
           deployment_target: str | None, resume: bool, reconcile: bool,
           instance_tier: str | None) -> None:
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
        _deploy(lab, assume_yes=True, reconcile=reconcile)
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

    # AWS from a laptop = control plane: provision EC2, then remote-run up.
    # On the EC2 guest itself (IMDS), fall through to a normal local deploy
    # with baremetal phase behaviour.
    aws_control_plane = (
        deployment_target == "aws" and not resume and not on_ec2()
    )

    if not aws_control_plane:
        _print_host(host)
        # 1. Host dependencies.
        if not _ensure_host_ready(host, assume_yes):
            raise SystemExit(1)
        host = detect_host()  # re-read after any install
    else:
        console.print(
            "[dim]AWS control plane — will provision EC2 and remote-run "
            "rodeo up --target baremetal on the instance.[/dim]\n"
        )

    # 2. A lab to deploy.
    lab_ready = lab is not None and (
        (lab / "rodeo-plan.yaml").exists() or (lab / "definition.yaml").exists()
    )
    if not lab_ready:
        if aws_control_plane:
            # Laptop has no nested-KVM sizing; require an explicit profile.
            chosen = profile or "harvester"
        else:
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
                lab = _default_labs_root() / lab_name
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

    # 4. Confirm + deploy.
    if not assume_yes and not Confirm.ask("\nDeploy now? (takes ~1-2 h on nested KVM)", default=True):
        console.print("Nothing deployed. Re-run [bold]rodeo up[/bold] when ready.")
        return

    if aws_control_plane:
        assert lab is not None
        _aws_control_plane_deploy(
            lab,
            profile=profile,
            instance_tier=instance_tier,
            assume_yes=assume_yes,
        )
        return

    # On EC2 with deployment_target aws: run local phases as baremetal.
    local_target = "baremetal" if deployment_target == "aws" else deployment_target
    if deployment_target == "aws" and lab is not None:
        _plan_path = lab / "rodeo-plan.yaml"
        if _plan_path.exists():
            import yaml as _yaml
            _plan_data = _yaml.safe_load(_plan_path.read_text()) or {}
            # Keep aws in the plan for destroy --cloud; phases use baremetal below.
            if _plan_data.get("deployment_target") != "aws":
                _plan_data["deployment_target"] = "aws"
                _plan_path.write_text(_yaml.dump(_plan_data, default_flow_style=False))

    if not is_root():
        console.print("\n[bold]Switching to root for the install[/bold] (sudo)…")
        resume_args = ["up", "--resume", "--dir", str(lab), "--yes",
                       "--target", local_target if deployment_target != "aws" else "baremetal"]
        resume_args.append("--reconcile" if reconcile else "--no-reconcile")
        ensure_root(resume_args)  # does not return

    _deploy(lab, assume_yes=assume_yes, reconcile=reconcile)


def _aws_control_plane_deploy(
    lab: Path,
    *,
    profile: str | None,
    instance_tier: str | None,
    assume_yes: bool,
) -> None:
    """Provision EC2 + remote ``rodeo up --target baremetal`` from the laptop."""
    try:
        cfg = load_config("rodeo-plan.yaml", config_dir=str(lab))
    except ValueError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    # Resolve lab profile name for the instance catalog (not engine type).
    lab_profile = profile or _infer_lab_profile(lab) or "harvester"
    try:
        cfg = _resolve_aws_instance_choice(
            cfg,
            lab=lab,
            lab_profile=lab_profile,
            instance_tier=instance_tier,
            assume_yes=assume_yes,
        )
        validate_config(cfg)
    except (ConfigError, ValueError) as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    from ..host_context import apply_host_context

    cfg, hc_notes = apply_host_context(cfg, host_facts={})
    for line in hc_notes:
        console.print(f"[dim]host-context: {line}[/dim]")
    # Persist overlays into the lab plan so the remote host inherits disk_gb / nvme.
    _persist_plan_overlays(lab, cfg)

    provider = cfg.get("provider") or {}
    console.print(
        f"\n[bold]Provisioning AWS KVM host[/bold]  "
        f"[cyan]{provider.get('instance_type')}[/cyan] "
        f"in [cyan]{provider.get('region')}[/cyan]…"
    )
    try:
        # Hint catalog profile for AwsHostProvider.apply_instance_selection
        if isinstance(cfg.get("provider"), dict):
            cfg["provider"] = {**cfg["provider"], "lab_profile": lab_profile}
        provisioned = execute_aws_up(cfg, profile=profile or lab_profile)
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    ip = provisioned.public_ip
    console.print(
        f"\n[green]✓[/green]  Remote deploy finished on [cyan]{ip}[/cyan] "
        f"(instance {provisioned.provider_id or '—'}).\n"
        f"  Harvester UI:  https://{ip}:8443\n"
        f"  Rancher UI:    https://{ip}:30002\n"
        f"  Tear down host:  rodeo destroy --cloud --yes "
        f"(from this lab dir)\n"
    )


def _infer_lab_profile(lab: Path) -> str | None:
    """Best-effort profile name from lab directory or plan name."""
    name = lab.name.strip()
    from ..labseed import PROFILE_EXAMPLE

    if name in PROFILE_EXAMPLE:
        return name
    plan = lab / "rodeo-plan.yaml"
    if plan.is_file():
        import yaml as _yaml

        data = _yaml.safe_load(plan.read_text()) or {}
        pname = str(data.get("name") or "")
        for key in PROFILE_EXAMPLE:
            if key in pname:
                return key
    return None


def _resolve_aws_instance_choice(
    cfg: dict,
    *,
    lab: Path,
    lab_profile: str,
    instance_tier: str | None,
    assume_yes: bool,
) -> dict:
    """Pick instance_type from explicit type, CLI tier, prompt, or recommended."""
    from ..providers.aws import AwsHostProvider
    from ..providers.instance_catalog import (
        TIERS,
        catalog_for_profile,
        normalize_tier,
        resolve_instance_type,
    )

    provider = dict(cfg.get("provider") or {})
    explicit = str(provider.get("instance_type") or "").strip()
    tier_cli = instance_tier or str(provider.get("instance_tier") or "").strip() or None

    if not explicit and not tier_cli and not assume_yes:
        catalog = catalog_for_profile(lab_profile)
        console.print(f"\n[bold]AWS instance size[/bold] for profile [cyan]{lab_profile}[/cyan]:\n")
        for i, tier in enumerate(TIERS, start=1):
            offer = catalog[tier]
            mark = " (default)" if tier == "recommended" else ""
            console.print(
                f"  {i}) [bold]{tier}[/bold]{mark}  "
                f"[cyan]{offer.instance_type}[/cyan]  — {offer.notes}"
            )
        choice = Prompt.ask(
            "\nPick a size",
            choices=["1", "2", "3", "budget", "recommended", "performance"],
            default="2",
        )
        if choice in ("1", "2", "3"):
            tier_cli = TIERS[int(choice) - 1]
        else:
            tier_cli = normalize_tier(choice)

    itype, tier_used = resolve_instance_type(
        profile=lab_profile,
        instance_type=explicit or None,
        instance_tier=tier_cli,
    )
    provider["instance_type"] = itype
    if tier_used:
        provider["instance_tier"] = tier_used
    provider["lab_profile"] = lab_profile
    cfg = {**cfg, "provider": provider}

    # Persist so re-runs and destroy keep the same type.
    _persist_plan_overlays(lab, cfg)

    # Pre-flight availability (also runs again inside provision if creating).
    AwsHostProvider().assert_available(provider, count=1)
    console.print(
        f"[green]✓[/green]  {itype} available in {provider.get('region')} "
        f"(profile={lab_profile}"
        + (f", tier={tier_used}" if tier_used else "")
        + ")."
    )
    return cfg


def _persist_plan_overlays(lab: Path, cfg: dict) -> None:
    """Write host-context resource/storage overlays back into rodeo-plan.yaml."""
    import yaml as _yaml

    plan_path = lab / "rodeo-plan.yaml"
    if not plan_path.is_file():
        return
    data = _yaml.safe_load(plan_path.read_text()) or {}
    if not isinstance(data, dict):
        return
    if isinstance(cfg.get("resources"), dict):
        data["resources"] = cfg["resources"]
    if isinstance(cfg.get("storage"), dict):
        storage = data.setdefault("storage", {})
        if isinstance(storage, dict):
            storage.update({k: v for k, v in cfg["storage"].items() if k in ("backend",)})
    if isinstance(cfg.get("libvirt"), dict):
        libvirt = data.setdefault("libvirt", {})
        if isinstance(libvirt, dict):
            for key in ("disk_cache", "disk_io"):
                if key in cfg["libvirt"]:
                    libvirt[key] = cfg["libvirt"][key]
    plan_path.write_text(_yaml.safe_dump(data, sort_keys=False, default_flow_style=False))


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


def _deploy(lab: Path, assume_yes: bool, reconcile: bool = True) -> None:
    """Load the lab, preflight, and run the pipeline (called as root)."""
    try:
        cfg = load_config("rodeo-plan.yaml", config_dir=str(lab))
        validate_config(cfg)
    except ValueError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    from ..host_context import apply_host_context, persist_host_context_notes
    from ..preflight import detect_host as _detect_host

    host_info = _detect_host()
    cfg, hc_notes = apply_host_context(
        cfg,
        host_facts={
            "cpus": host_info.get("cpus") or 0,
            "disk_free_gib": host_info.get("disk_free_gib"),
            "image_dir": host_info.get("image_dir"),
        },
    )
    persist_host_context_notes(cfg, hc_notes)

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

    code = execute_deploy(cfg, root, reconcile=reconcile)
    raise SystemExit(code)
