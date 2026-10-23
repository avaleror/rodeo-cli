"""rodeo content — a lab's instructions, deployed apart from its infrastructure (see rodeo/content.py)."""
from __future__ import annotations

from pathlib import Path

import click
import yaml
from rich.console import Console

from .. import content
from ..config import ConfigError, find_lab_dir, load_config
from ._options import config_options

console = Console()


def _lab(cfg: dict) -> Path:
    if cfg.get("config_dir"):
        return Path(cfg["config_dir"])
    detected = find_lab_dir()
    return detected if detected else Path.cwd()


def _load(config_path: str, config_dir: str | None, params: tuple[str, ...], paramfile: str | None) -> dict:
    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")
    return load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)


@click.group("content")
def content_cmd() -> None:
    """The lab's instructions: the plan's content: block, fetched and rendered on their own."""


@content_cmd.command("apply")
@config_options
def content_apply_cmd(config_path: str, config_dir: str | None, params: tuple[str, ...], paramfile: str | None) -> None:
    """Fetch the plan's content: into <lab>/content/ and render <lab>/guide/, without redeploying.

    \b
    Run it from the lab directory, again whenever the content repository changes.
    """
    cfg = _load(config_path, config_dir, params, paramfile)
    lab = _lab(cfg)
    try:
        result = content.apply(cfg, lab)
    except (ConfigError, RuntimeError, OSError) as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)
    spec = content.content_spec(cfg)
    console.print(f"[green]✓  Instructions from {spec['repo']}[/green]"
                  + (f" @ {result['commit'][:12]}" if result["commit"] else ""))
    for path in result["files"]:
        console.print(f"   {path}")


@content_cmd.command("show")
@config_options
def content_show_cmd(config_path: str, config_dir: str | None, params: tuple[str, ...], paramfile: str | None) -> None:
    """Show the plan's content: block and what was last fetched."""
    cfg = _load(config_path, config_dir, params, paramfile)
    try:
        spec = content.content_spec(cfg)
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)
    if spec is None:
        console.print("This plan has no content: block: its instructions are the lab's story/ or workshop: block.")
        return
    console.print(f"repo: {spec['repo']}\nref: {spec['ref'] or '(default branch)'}\npath: {spec['path'] or '(root)'}"
                  f"\nformat: {spec['format']}")
    lock = _lab(cfg) / content.LOCK
    if lock.is_file():
        data = yaml.safe_load(lock.read_text()) or {}
        console.print(f"fetched: {data.get('commit') or '(unknown commit)'}  ({lock})")
    else:
        console.print("not fetched yet: rodeo content apply")
