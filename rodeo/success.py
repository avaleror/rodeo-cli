"""The 'you did it' screen, shown after a successful deploy.

This is the payoff moment a learner is waiting for: where to log in, with which
credentials, and the first thing to try. Shared by ``rodeo deploy`` and ``rodeo up``.
"""
from __future__ import annotations

import subprocess

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


def _host_ip() -> str:
    """Return the IP on the default-route interface, or a placeholder."""
    try:
        r = subprocess.run(
            ["ip", "route", "get", "8.8.8.8"],
            capture_output=True, text=True, timeout=5,
        )
        parts = r.stdout.split()
        if "src" in parts:
            return parts[parts.index("src") + 1]
    except Exception:
        pass
    return "<host-ip>"


def _read_passwords() -> tuple[str, str]:
    """Return (harvester_admin_password, rancher_admin_password) from secrets file."""
    harvester_pw = rancher_pw = ""
    try:
        from pathlib import Path
        for line in (Path.home() / ".rodeo" / "secrets.yaml").read_text().splitlines():
            if line.startswith("harvester_admin_password:"):
                harvester_pw = line.split(":", 1)[1].strip().strip("\"'")
            elif line.startswith("rancher_admin_password:"):
                rancher_pw = line.split(":", 1)[1].strip().strip("\"'")
            elif not harvester_pw and line.startswith("lab_admin_password:"):
                # backward compat with old secrets files
                val = line.split(":", 1)[1].strip().strip("\"'")
                harvester_pw = harvester_pw or val
                rancher_pw = rancher_pw or val
    except Exception:
        pass
    return harvester_pw or "(see ~/.rodeo/secrets.yaml)", rancher_pw or "(see ~/.rodeo/secrets.yaml)"


def render_success(cfg: dict) -> None:
    """Print the success panel with access URLs, credentials, and next steps.

    Topology-aware: only shows the Harvester UI / Rancher lines for the components
    the deployed profile actually includes.

    Target-aware: baremetal detects the host IP and shows the DNAT'd ports; Instruqt
    shows internal IPs and points the user to the Instruqt tab in the lab UI.
    """
    target = cfg.get("deployment_target", "baremetal")
    net = cfg.get("network", {})
    vip = net.get("vip", "192.168.122.10")
    rancher_ip = net.get("rancher_ip", "192.168.122.9")
    rancher_nodeport = int(net.get("rancher_nodeport", 30002))
    harvester_ui_port = 8443

    profile_type = cfg.get("type", "")
    is_suse_edge = profile_type == "suse-edge"

    vms = cfg.get("vms", {})
    harvester_nodes = [n for n in vms if n not in ("rancher", "eib") and not n.startswith("edge")]
    has_harvester = bool(harvester_nodes)
    has_rancher = _has_rancher(cfg)

    tls = cfg.get("rancher_tls", {})
    tls_source = tls.get("source", "secret")
    # For letsEncrypt: hostname is based on the external host IP, not the VM IP.
    # rancher.py computes this at install time but doesn't persist it back to cfg,
    # so we recompute it here the same way.
    _ext_ip = cfg.get("rancher_hostname") or _host_ip()
    rancher_hostname = f"rancher.{_ext_ip.replace('.', '-')}.sslip.io"

    lines: list[str] = []
    lines.append("[bold green]Your lab is up.[/bold green]\n")

    lines.append("[bold]Open in a browser[/bold] (accept the self-signed cert):")

    if target == "instruqt":
        if has_harvester:
            lines.append(f"  Harvester UI   https://{vip}  (from the host)")
            lines.append("                 External: use the Harvester tab in the Instruqt lab UI")
        if has_rancher:
            if tls_source == "letsEncrypt":
                lines.append(f"  Rancher Prime  https://{rancher_hostname}  (Let's Encrypt cert)")
            else:
                lines.append(f"  Rancher Prime  https://{rancher_ip}:{rancher_nodeport}  (from the host)")
            lines.append("                 External: use the Rancher tab in the Instruqt lab UI")
        lines.append("")
        if has_harvester:
            lines.append(
                f"[dim]Instruqt tabs need host ports {harvester_ui_port} (Harvester) "
                f"and {rancher_nodeport} (Rancher) declared as services in the track config.[/dim]"
            )
        elif has_rancher:
            lines.append(
                f"[dim]Instruqt tab needs host port {rancher_nodeport} (Rancher) "
                "declared as a service in the track config.[/dim]"
            )
    else:
        host = _host_ip()
        if has_harvester:
            lines.append(f"  Harvester UI   https://{vip}")
            lines.append(f"  [dim](external: host port {harvester_ui_port} → DNAT → VIP, i.e. https://{host}:{harvester_ui_port})[/dim]")
        if has_rancher:
            if tls_source == "letsEncrypt":
                lines.append(f"  Rancher Prime  https://{rancher_hostname}")
                lines.append("  [dim](Let's Encrypt cert via sslip.io — ports 80 + 443 must be reachable from internet)[/dim]")
            else:
                lines.append(f"  Rancher Prime  https://{rancher_ip}:{rancher_nodeport}")
                lines.append(f"  [dim](external: https://{host}:{rancher_nodeport})[/dim]")

    harvester_pw, rancher_pw = _read_passwords()
    lines.append("")
    lines.append("[bold]Log in[/bold]")
    lines.append("  user                admin")
    if has_harvester:
        lines.append(f"  Harvester password  {harvester_pw}")
    if has_rancher:
        lines.append(f"  Rancher password    {rancher_pw}")
    lines.append("  [dim](also in ~/.rodeo/secrets.yaml and $HARVESTER_ADMIN_PASSWORD / $RANCHER_ADMIN_PASSWORD)[/dim]")

    lines.append("")
    if is_suse_edge:
        edge_nodes = [(n, v) for n, v in vms.items() if n.startswith("edge")]
        if edge_nodes:
            lines.append("[bold]Edge node reference[/bold]  (static DHCP — MAC determines IP)")
            lines.append("  node    MAC                  IP")
            for name, info in sorted(edge_nodes):
                mac = info.get("mac", "—")
                ip  = info.get("ip", "—")
                lines.append(f"  {name:<7} {mac:<20} {ip}  (DHCP pre-assigned)")
            lines.append("")

    lines.append("[bold]First things to try[/bold]")
    lines.append("  rodeo status                 # health + phase progress")
    if is_suse_edge:
        lines.append("  rodeo ssh eib            # shell into the EIB VM (build Elemental OS images here)")
        lines.append("  On the eib VM: edit /home/eib-config/edge-definition.yaml")
        lines.append("    → replace REPLACE_WITH_REGISTRATION_URL with the MachineRegistration URL")
        lines.append("    → run EIB to build the Elemental OS image (base OS from Hauler: http://localhost:8080)")
        lines.append("  From the KVM host: rodeo pull-edge-image   # seed edge1/2/3 boot disks")
        lines.append("  rodeo start edge1 edge2 edge3              # boot edge nodes into Elemental")
        lines.append("  In Rancher: Fleet → Git Repos → alien-geeko is waiting for edge clusters")
        lines.append("    → label your edge cluster: demo=true  edge-type=x86-cluster")
    else:
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
