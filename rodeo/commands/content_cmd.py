"""rodeo content — a lab's instructions, deployed apart from its infrastructure (see rodeo/content.py)."""
from __future__ import annotations

from pathlib import Path
from typing import Collection

import click
import yaml
from rich.console import Console

from .. import content
from ..config import ConfigError, find_lab_dir, load_config
from ._options import config_options

console = Console()


def report_apply(cfg: dict, lab: Path, result: dict, *, setup: bool = True, skip: Collection[str] = ()) -> int:
    """Print what content.apply did, then run the content's setup scripts (unless *setup* is
    False) but those named in *skip*; returns how many setup scripts failed."""
    spec = content.content_spec(cfg)
    console.print(f"[green]✓  Instructions from {spec['repo']}[/green]"
                  + (f" ({result['ref']})" if result.get("ref") else "")
                  + (f" @ {result['commit'][:12]}" if result["commit"] else ""))
    for warning in result.get("language", []):
        console.print(f"[yellow]⚠  {warning}[/yellow]", highlight=False)
    for path in result["files"]:
        console.print(f"   {path}")
    if result["checks"]:
        console.print(f"[green]✓  Checks installed in {result['checks']}[/green] (any user on this host can run them)")
    for warning in result["warnings"]:
        console.print(f"[yellow]⚠  Checks not installed: {warning}[/yellow]")
    if not setup:
        if content.setup_scripts(lab):
            console.print("[yellow]⚠  The content's setup scripts were left out: deployment_target is 'instruqt' "
                          "and they belong in the baked image. Run them with: rodeo content apply[/yellow]")
        return 0
    for script in content.setup_scripts(lab):
        if script.name in skip:
            console.print(f"  [dim]skip[/dim]  {script.name} (the profile's own copy ran)")
    failed = 0
    for name, code, lines in content.run_setup(cfg, lab, skip=skip):
        console.print(f"Running content setup script {name}...")
        for line in lines:
            console.print(f"  {line}", markup=False, highlight=False)
        if code == 0:
            console.print(f"  [green]✓[/green]  {name}")
        else:
            failed += 1
            console.print(f"  [red]✗[/red]  {name} " + (f"exited {code}" if code > 0 else "did not finish"))
    return failed


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
    """Fetch the plan's content: into <lab>/content/, render <lab>/guide/, install its checks/
    and run its custom/scripts/, without redeploying.

    \b
    Run it from the lab directory, again whenever the content repository changes.
    Installing the checks into /opt/rodeo/<plan name>/checks needs root.
    """
    cfg = _load(config_path, config_dir, params, paramfile)
    lab = _lab(cfg)
    try:
        result = content.apply(cfg, lab)
    except (ConfigError, RuntimeError, OSError) as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)
    if report_apply(cfg, lab, result):
        raise SystemExit(1)


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
        scripts = content.setup_scripts(_lab(cfg))
        console.print("setup scripts: " + (", ".join(s.name for s in scripts) or "(none)"))
    else:
        console.print("not fetched yet: rodeo content apply")
    try:
        checks = content.checks_dir(cfg)
    except ConfigError:
        return
    console.print(f"checks: {checks}" if checks.is_dir() else f"checks: not installed ({checks})")
