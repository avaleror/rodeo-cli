"""rodeo init — scaffold a rodeo-plan.yaml and ~/.rodeo/secrets.yaml."""
from __future__ import annotations

import shutil
import stat
from pathlib import Path

import click
from rich.console import Console

console = Console()

_TEMPLATES = Path(__file__).parent.parent / "data" / "templates"


@click.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing files.")
@click.argument("target_dir", default=".", type=click.Path())
def init_cmd(force: bool, target_dir: str) -> None:
    """Generate rodeo-plan.yaml and ~/.rodeo/secrets.yaml from templates."""
    dest = Path(target_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    plan_dest = dest / "rodeo-plan.yaml"
    secrets_dest = Path.home() / ".rodeo" / "secrets.yaml"

    if plan_dest.exists() and not force:
        console.print(f"[yellow]{plan_dest} already exists — use --force to overwrite.[/yellow]")
    else:
        shutil.copy(_TEMPLATES / "rodeo-plan.yaml", plan_dest)
        console.print(f"[green]✓[/green]  {plan_dest}")

    secrets_dest.parent.mkdir(parents=True, exist_ok=True)
    if secrets_dest.exists() and not force:
        console.print(f"[yellow]{secrets_dest} already exists — use --force to overwrite.[/yellow]")
    else:
        shutil.copy(_TEMPLATES / "secrets.yaml", secrets_dest)
        secrets_dest.chmod(stat.S_IRUSR | stat.S_IWUSR)  # chmod 600
        console.print(f"[green]✓[/green]  {secrets_dest}  [dim](chmod 600)[/dim]")

    console.print(
        "\nEdit [bold]rodeo-plan.yaml[/bold] for your environment, "
        "then set passwords in [bold]~/.rodeo/secrets.yaml[/bold]."
    )
    console.print("Run [bold]rodeo deploy[/bold] when ready.")
