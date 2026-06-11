"""rodeo deploy — orchestrate the full Harvester + Rancher pipeline."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from ..config import load_config, find_ansible_root
from ..state import PHASES, is_phase_done, mark_phase_done, mark_phase_failed, reset_from

console = Console()


# ---------- Ansible helpers ----------

def _build_extra_vars(cfg: dict) -> list[str]:
    creds = cfg.get("credentials", {})
    net = cfg.get("network", {})
    return [
        "-e", f"network_mode={net.get('mode', 'nat')}",
        "-e", f"host_bridge={net.get('host_bridge', 'br0')}",
        "-e", f"harvester_vip={net.get('vip', '192.168.122.10')}",
        "-e", f"rancher_ip={net.get('rancher_ip', '192.168.122.9')}",
        "-e", f"harvester_os_password={creds.get('harvester_os_password', '')}",
        "-e", f"rancher_vm_password={creds.get('harvester_os_password', '')}",
    ]


def _run_ansible(root: "Path", tags: str, cfg: dict, extra: list[str] | None = None) -> int:
    from pathlib import Path

    inventory = root / cfg["ansible"]["inventory"]
    playbook = root / "ansible" / "playbook.yml"
    cmd = [
        "ansible-playbook",
        "-i", str(inventory),
        str(playbook),
        "--tags", tags,
    ] + _build_extra_vars(cfg) + (extra or [])
    return subprocess.run(cmd).returncode


# ---------- Phase runners (plain Rich mode) ----------

def _phase_ansible(phase: str, tags: str, root, cfg: dict) -> None:
    console.rule(f"[bold cyan]{phase}[/bold cyan]")
    rc = _run_ansible(root, tags, cfg)
    if rc != 0:
        mark_phase_failed(phase, f"ansible exited {rc}")
        console.print(f"[red]✗  {phase} failed (exit {rc}).[/red]")
        raise SystemExit(rc)
    mark_phase_done(phase)
    console.print(f"  [green]✓[/green]  {phase}")


def _phase_cluster(cfg: dict, root) -> None:
    """Start VMs and wait for Harvester cluster to be fully ready."""
    console.rule("[bold cyan]cluster[/bold cyan]")
    net = cfg["network"]
    vip = net["vip"]
    deployer = root / "deployer"

    # Start firewalld (Ansible only writes permanent rules, does not start it)
    console.print("  Starting firewalld...")
    subprocess.run(["systemctl", "start", "firewalld"], check=False)
    subprocess.run(["firewall-cmd", "--reload"], check=False)

    # Start VMs via the bundled start-vms.sh
    start_script = deployer / "lib" / "start-vms.sh"
    if start_script.exists():
        env = {**os.environ, "HARVESTER_VIP": vip}
        rc = subprocess.run([str(start_script)], env=env).returncode
        if rc != 0:
            mark_phase_failed("cluster", f"start-vms.sh exited {rc}")
            console.print("[red]✗  cluster: start-vms.sh failed[/red]")
            raise SystemExit(rc)
    else:
        console.print("[yellow]  ⚠  start-vms.sh not found — starting VMs manually[/yellow]")
        _start_vms_direct(cfg)

    mark_phase_done("cluster")
    console.print("  [green]✓[/green]  cluster")


def _start_vms_direct(cfg: dict) -> None:
    """Fallback: start VMs via libvirt-python and poll VIP."""
    import ssl, urllib.request

    from ..engine.libvirt import LibvirtDriver, RODEO_VMS

    with LibvirtDriver(cfg["libvirt"]["uri"]) as lv:
        lv.net_start()
        for vm in RODEO_VMS:
            lv.start(vm)

    vip = cfg["network"]["vip"]
    console.print(f"  Waiting for VIP {vip} (up to 60 min)...")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    deadline = time.time() + 3600
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"https://{vip}", timeout=10, context=ctx)
            break
        except Exception:
            time.sleep(30)
    else:
        mark_phase_failed("cluster", "VIP timeout")
        raise SystemExit(1)


def _phase_rancher(cfg: dict, root) -> None:
    """Run setup-rancher.sh inside the Rancher VM."""
    console.rule("[bold cyan]rancher[/bold cyan]")
    script = root / "deployer" / "lib" / "setup-rancher.sh"
    creds = cfg.get("credentials", {})
    net = cfg["network"]
    ver = cfg.get("versions", {})

    env = {
        **os.environ,
        "RANCHER_VM_IP":         net.get("rancher_ip", "192.168.122.9"),
        "RANCHER_VERSION":       ver.get("rancher", "2.13.1"),
        "K3S_VERSION":           ver.get("k3s", "v1.31.4+k3s1"),
        "HARVESTER_VIP":         net.get("vip", "192.168.122.10"),
        "HARVESTER_OS_PASSWORD": creds.get("harvester_os_password", ""),
        "CERT_MANAGER_VERSION":  ver.get("cert_manager", "v1.16.2"),
        "LAB_ADMIN_PASSWORD":    creds.get("lab_admin_password", creds.get("harvester_os_password", "")),
    }
    rc = subprocess.run([str(script)], env=env).returncode
    if rc != 0:
        mark_phase_failed("rancher", f"setup-rancher.sh exited {rc}")
        console.print("[red]✗  rancher failed[/red]")
        raise SystemExit(rc)
    mark_phase_done("rancher")
    console.print("  [green]✓[/green]  rancher")


def _phase_finalise(cfg: dict) -> None:
    console.rule("[bold cyan]finalise[/bold cyan]")
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
    console.print("  [green]✓[/green]  finalise")


# ---------- CLI command ----------

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
def deploy_cmd(
    config_path: str,
    from_phase: str | None,
    ansible_path: str | None,
    tui: bool | None,
    install_collections: bool,
) -> None:
    """Deploy the full SUSE Virtualization Rodeo cluster."""
    from pathlib import Path

    cfg = load_config(config_path)

    root = Path(ansible_path) if ansible_path else find_ansible_root(cfg)
    if root is None or not (root / "ansible" / "playbook.yml").exists():
        console.print(
            "[red]Cannot find ansible/playbook.yml.[/red]\n"
            "Set [bold]ansible.path[/bold] in rodeo-plan.yaml, "
            "use [bold]--ansible-path[/bold], "
            "or set [bold]RODEO_ANSIBLE_PATH[/bold]."
        )
        raise SystemExit(1)

    # Decide TUI vs plain
    use_tui = sys.stdout.isatty() if tui is None else tui
    if use_tui:
        try:
            from ..app import RodeoApp
            app = RodeoApp(
                cfg=cfg,
                ansible_root=root,
                from_phase=from_phase,
                install_collections=install_collections,
            )
            app.run()
            return
        except ImportError:
            console.print("[yellow]⚠  textual not installed — falling back to plain output[/yellow]")

    # Plain Rich mode
    _deploy_plain(cfg, root, from_phase, install_collections)


def _deploy_plain(cfg: dict, root, from_phase: str | None, install_collections: bool) -> None:
    from pathlib import Path

    if from_phase:
        reset_from(from_phase)

    phases = PHASES
    start_idx = phases.index(from_phase) if from_phase and from_phase in phases else 0

    console.print(f"\n[bold]rodeo deploy[/bold]  [{root}]\n")

    # Install Ansible collections once (only needed before first Ansible phase)
    if install_collections and start_idx <= phases.index("vms"):
        req_file = root / "ansible" / "requirements.yml"
        if req_file.exists():
            console.print("  Installing Ansible collections...")
            subprocess.run(
                ["ansible-galaxy", "collection", "install", "-r", str(req_file)],
                check=False,
            )

    for idx, phase in enumerate(phases):
        if idx < start_idx or is_phase_done(phase):
            if idx < start_idx:
                console.print(f"  [dim]skip[/dim]  {phase}")
            else:
                console.print(f"  [dim]done[/dim]  {phase} (already complete)")
            continue

        if phase == "kvm_host":
            _phase_ansible("kvm_host", "kvm_host", root, cfg)
        elif phase == "vms":
            _phase_ansible("vms", "vms", root, cfg)
        elif phase == "cluster":
            _phase_cluster(cfg, root)
        elif phase == "rancher":
            _phase_rancher(cfg, root)
        elif phase == "finalise":
            _phase_finalise(cfg)

    net = cfg["network"]
    console.print(f"\n[bold green]✓  Deployment complete.[/bold green]")
    console.print(f"  Harvester:  https://{net['vip']}")
    console.print(f"  Rancher:    https://{net['rancher_ip']}:30002\n")
