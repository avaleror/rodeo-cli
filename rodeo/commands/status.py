"""rodeo status — one-shot cluster health table."""
from __future__ import annotations

import ssl
import urllib.error
import urllib.request
import click
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..config import load_config
from ..profiles import get_profile
from ..state import load_state

console = Console()

_VM_COLOR = {
    "running":      "green",
    "shut off":     "red",
    "not found":    "dim",
    "shutting down": "yellow",
    "paused":       "yellow",
    "crashed":      "bold red",
    "blocked":      "yellow",
    "suspended":    "yellow",
}


def _colored(label: str, default: str = "white") -> Text:
    return Text(label, style=_VM_COLOR.get(label, default))


def _vip_reachable(vip: str) -> bool:
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        urllib.request.urlopen(f"https://{vip}", timeout=5, context=ctx)
        return True
    except urllib.error.HTTPError:
        return True  # any HTTP status means the VIP answered
    except Exception:
        return False


@click.command("status")
@click.option("--config", "config_path", default="rodeo-plan.yaml", show_default=True)
def status_cmd(config_path: str) -> None:
    """Show VM states and cluster reachability at a glance."""
    cfg = load_config(config_path)
    profile = get_profile(cfg.get("type", "suse-virt"))
    state = load_state(cfg.get("name", "default"))
    vip = cfg["network"]["vip"]

    # VM table
    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0, 1))
    table.add_column("VM", style="bold", min_width=12)
    table.add_column("State", min_width=10)
    table.add_column("Autostart", justify="center")

    try:
        from ..engine.libvirt import LibvirtDriver

        with LibvirtDriver(cfg["libvirt"]["uri"]) as lv:
            vms = lv.list_vms(list(cfg.get("vms", {}).keys()) or profile.vm_names)
    except RuntimeError as exc:
        console.print(f"[yellow]⚠  {exc}[/yellow]\n")
        vms = []

    for vm in vms:
        table.add_row(
            vm.name,
            _colored(vm.state),
            "[green]✓[/green]" if vm.autostart else "[dim]—[/dim]",
        )

    console.print()
    console.print("[bold]  VMs[/bold]")
    console.print(table)

    # VIP
    vip_ok = _vip_reachable(vip)
    vip_style = "green" if vip_ok else "red"
    vip_label = "reachable" if vip_ok else "unreachable"
    console.print(
        f"\n  [bold]Cluster VIP[/bold]  "
        f"[{vip_style}]{vip} — {vip_label}[/{vip_style}]\n"
    )

    # Phase progress
    if state.get("phases"):
        console.print("  [bold]Phases[/bold]")
        for phase in profile.phases:
            info = state["phases"].get(phase, {})
            if info.get("completed"):
                icon = "[green]✓[/green]"
                ts = info.get("timestamp", "")[:19].replace("T", " ")
                console.print(f"    {icon}  {phase:<12}  [dim]{ts}[/dim]")
            elif info.get("last_error"):
                console.print(f"    [red]✗[/red]  {phase}")
            else:
                console.print(f"    [dim]○[/dim]  {phase}")
        console.print()
