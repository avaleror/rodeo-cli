"""rodeo restart <vm> — graceful shutdown then start."""
from __future__ import annotations

import time

import click
from rich.console import Console

console = Console()


@click.command("restart")
@click.argument("vm")
@click.option("--config", "config_path", default="rodeo-plan.yaml", show_default=True)
@click.option("--config-dir", "config_dir", default=None, metavar="DIR", type=click.Path(file_okay=False, dir_okay=True, exists=False))
@click.option("--hard", is_flag=True, help="Force-kill instead of ACPI shutdown.")
def restart_cmd(vm: str, config_path: str, config_dir: str | None, hard: bool) -> None:
    """Restart a VM (ACPI shutdown + start). Use 'all' to cycle every VM."""
    from ..config import load_config
    from ..engine.libvirt import LibvirtDriver

    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")
    cfg = load_config(config_path, config_dir=config_dir)
    vm_names = list(cfg.get("vms", {}).keys())
    if vm != "all" and vm not in vm_names:
        console.print(f"[red]✗  Unknown VM '{vm}'. Known: {', '.join(vm_names + ['all'])}[/red]")
        raise SystemExit(1)
    targets = vm_names if vm == "all" else [vm]

    with LibvirtDriver(cfg["libvirt"]["uri"]) as lv:
        for name in targets:
            info = lv.get_vm(name)
            if info.state == "not found":
                console.print(f"[yellow]  {name}: not found, skipping[/yellow]")
                continue

            if info.state == "running":
                console.print(f"  [dim]shutting down[/dim]  {name}...", end="")
                if hard:
                    lv.destroy(name)
                else:
                    lv.shutdown(name)
                # wait up to 90s for clean stop
                for _ in range(90):
                    time.sleep(1)
                    if not lv.is_running(name):
                        break
                console.print(" [green]stopped[/green]")

            console.print(f"  [dim]starting[/dim]      {name}...", end="")
            lv.start(name)
            console.print(" [green]started[/green]")

    console.print()
