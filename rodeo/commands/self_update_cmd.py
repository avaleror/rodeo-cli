"""rodeo self-update — pull latest code and reinstall the CLI in-place."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console

console = Console()

# Repo root is three levels up from this file:
# rodeo/commands/self_update_cmd.py → rodeo/commands/ → rodeo/ → <repo root>
_REPO_ROOT = Path(__file__).parent.parent.parent.resolve()
_VENV_PIP  = _REPO_ROOT / ".venv" / "bin" / "pip"


@click.command("self-update")
def self_update_cmd() -> None:
    """Pull the latest rodeo-cli code and reinstall the CLI in-place.

    Equivalent to: git pull && pip install -e . inside the install directory.
    Run this after any upstream release to pick up new features and bug fixes.
    """
    if not (_REPO_ROOT / ".git").exists():
        console.print(
            f"[red]✗  {_REPO_ROOT} is not a git repo — cannot self-update.[/red]\n"
            "Re-run the install script to get a fresh clone:\n"
            "  curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash"
        )
        raise SystemExit(1)

    console.print(f"Updating rodeo-cli from [dim]{_REPO_ROOT}[/dim]...")

    r = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "pull", "--ff-only"],
        capture_output=False,
    )
    if r.returncode != 0:
        console.print("[red]✗  git pull failed — check the output above.[/red]")
        raise SystemExit(r.returncode)

    pip = str(_VENV_PIP) if _VENV_PIP.exists() else sys.executable.replace("rodeo", "pip")
    console.print("Reinstalling package...")
    r = subprocess.run(
        [pip, "install", "--quiet", "-e", str(_REPO_ROOT)],
        capture_output=False,
    )
    if r.returncode != 0:
        console.print("[red]✗  pip install failed.[/red]")
        raise SystemExit(r.returncode)

    from rodeo import __version__
    console.print(f"\n[bold green]✓  rodeo-cli updated to {__version__}[/bold green]")
