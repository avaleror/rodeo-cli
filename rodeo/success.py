"""The 'you did it' screen, shown after a successful deploy.

This is the payoff moment a learner is waiting for: where to log in, with which
credentials, and the first thing to try. Shared by ``rodeo deploy`` and ``rodeo up``.
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

console = Console()


def _has_rancher(cfg: dict) -> bool:
    vms = cfg.get("vms", {})
    if "rancher" in vms:
        return True
    for comp in cfg.get("components", []):
        if isinstance(comp, dict) and comp.get("name") == "rancher":
            return True
    return False


def render_success(cfg: dict) -> None:
    """Print the success panel with access URLs, credentials, and next steps.

    Topology-aware: only shows the Harvester UI / Rancher lines and hints for the
    components the deployed profile actually has.
    """
    net = cfg.get("network", {})
    vip = net.get("vip", "192.168.122.10")
    rancher_ip = net.get("rancher_ip", "192.168.122.9")
    vms = cfg.get("vms", {})
    harvester_nodes = [n for n in vms if n != "rancher"]
    has_harvester = bool(harvester_nodes)
    has_rancher = _has_rancher(cfg)

    lines: list[str] = []
    lines.append("[bold green]Your lab is up.[/bold green]\n")

    lines.append("[bold]Open in a browser[/bold] (accept the self-signed cert):")
    if has_harvester:
        lines.append(f"  Harvester UI   https://{vip}")
        lines.append("                 or  https://<this-host-ip>:8443  (from another machine)")
    if has_rancher:
        lines.append(f"  Rancher Prime  https://{rancher_ip}:30002")
        lines.append("                 or  https://<this-host-ip>:30002  (from another machine)")

    lines.append("")
    lines.append("[bold]Log in[/bold]")
    lines.append("  user      admin")
    lines.append("  password  see ~/.rodeo/secrets.yaml  (lab_admin_password)")

    lines.append("")
    lines.append("[bold]First things to try[/bold]")
    lines.append("  rodeo status                 # health + phase progress")
    ssh_target = harvester_nodes[0] if has_harvester else next(iter(vms), "rancher")
    lines.append(f"  rodeo ssh {ssh_target}{' ' * max(1, 16 - len(ssh_target))}# shell into the VM")
    if has_harvester:
        lines.append("  In Harvester: create a VM from an image, then watch it boot")
    elif has_rancher:
        lines.append("  In Rancher: explore Cluster Management and install an app from Charts")

    lines.append("")
    lines.append("[dim]Stop for later:  rodeo stop --all --yes   ·   Tear down:  rodeo clean --yes[/dim]")

    console.print()
    console.print(Panel("\n".join(lines), title="rodeo", border_style="green", expand=False))
    console.print()
