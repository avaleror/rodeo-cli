"""rodeo watch — live TUI: VM serial logs + current deploy state."""
from __future__ import annotations

import sys

import click
from rich.console import Console

console = Console()


@click.command("watch")
@config_options
def watch_cmd(config_path: str, config_dir: str | None, params: tuple[str, ...], paramfile: str | None, ansible_path: str | None) -> None:
    """Open the split-panel TUI to watch serial logs and phase state (no new deploy)."""
    if not sys.stdout.isatty():
        console.print("[red]✗  rodeo watch requires a TTY.[/red]")
        raise SystemExit(1)

    from pathlib import Path
    from ..config import load_config, find_ansible_root
    from ..app import RodeoApp

    cfg = load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)
    root = Path(ansible_path) if ansible_path else find_ansible_root(cfg)

    app = RodeoApp(
        cfg=cfg,
        ansible_root=root or Path("."),
        watch_only=True,
    )
    app.run()
