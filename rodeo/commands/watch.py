"""rodeo watch — live Textual TUI for deploy progress + serial logs (v0.2)."""
from __future__ import annotations

import click
from rich.console import Console

console = Console()


@click.command("watch")
@click.option("--config", "config_path", default="rodeo-plan.yaml", show_default=True)
def watch_cmd(config_path: str) -> None:
    """Launch the split-panel TUI: deploy progress on left, serial logs on right."""
    console.print("[yellow]rodeo watch (Textual TUI) is coming in v0.2.[/yellow]")
    console.print("For now: run [bold]rodeo deploy[/bold] in one terminal and [bold]rodeo logs <vm>[/bold] in another.")
