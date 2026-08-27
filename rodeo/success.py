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
    from .paths import rodeo_secrets_path

    harvester_pw = rancher_pw = ""
    try:
        for line in rodeo_secrets_path().read_text().splitlines():
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
    # The profile owns the narrative sections (see RodeoProfile.success_* hooks);
    # unknown types fall back to the generic next-steps.
    try:
        from .profiles import get_profile

        profile = get_profile(profile_type)
    except Exception:
        profile = None

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

    if target == "instruqt":
        lines.append("")
        lines.append("[bold]Instruqt hostimage checklist[/bold] (before Save)")
        lines.append("  • Do [bold]not[/bold] run:  rodeo deploy --finalise")
        lines.append(
            "    [dim]libvirt-guests + VM autostart in the image can stall boot "
            "and leave the console on \"Please Wait\".[/dim]"
        )
        lines.append("  • Confirm agent ports:  firewall-cmd --list-ports")
        lines.append(
            "    [dim]expect 15778/tcp and 15779/tcp (Instruqt terminal/editor agent).[/dim]"
        )
        lines.append("  • On every hostimage / attendee boot (track setup script):")
        lines.append("      rodeo start-if-needed")
        lines.append(
            "    [dim]starts VMs if needed and re-applies DNAT/nft — "
            "do not rely on finalise-in-image.[/dim]"
        )

    lines.append("")
    if profile is not None:
        lines.extend(profile.success_extra_sections(cfg))

    lines.append("[bold]First things to try[/bold]")
    lines.append("  rodeo status                 # health + phase progress")
    if profile is not None:
        lines.extend(profile.success_next_steps(cfg))
    else:
        from .profiles.base import default_success_next_steps

        lines.extend(default_success_next_steps(cfg))

    lines.append("")
    lines.append("[dim]Stop for later:  rodeo stop --all --yes   ·   Tear down:  rodeo clean --yes[/dim]")

    console.print()
    console.print(Panel("\n".join(lines), title="rodeo", border_style="green", expand=False))
    console.print()
