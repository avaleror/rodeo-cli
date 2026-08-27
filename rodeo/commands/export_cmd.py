"""rodeo export — render the lab spec in another deployer's input format.

First step of delegating deployment to lab-in-a-box: the rodeo plan +
definition stay the source of truth, and this command emits the lab.json
that lab-in-a-box's setup_lab.sh / destroy_lab.sh consume.
"""
from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console

from ..config import load_config
from ..labinabox import build_lab_json
from ._options import config_options

# Warnings and status go to stderr so stdout stays pipeable JSON.
console = Console(stderr=True)


@click.command("export")
@config_options
@click.option(
    "--format", "fmt", type=click.Choice(["lab-in-a-box"]), default="lab-in-a-box",
    show_default=True, help="Target deployer format.",
)
@click.option(
    "-o", "--output", default="-", metavar="FILE", show_default="stdout",
    help="Write the exported definition to FILE.",
)
@click.option(
    "--skip-unsupported", is_flag=True,
    help="Drop nodes the target deployer cannot build (e.g. PXE-booted "
         "Harvester nodes) instead of failing.",
)
def export_cmd(
    config_path: str,
    config_dir: str | None,
    params: tuple[str, ...],
    paramfile: str | None,
    fmt: str,
    output: str,
    skip_unsupported: bool,
) -> None:
    """Export the lab as a lab-in-a-box lab.json.

    \b
    Deploy the exported lab from a lab-in-a-box automation node:
      rodeo export -o lab.json
      setup_lab.sh lab.json        # destroy_lab.sh lab.json to tear down
    \b
    lab-in-a-box specific knobs live under lab_in_a_box: in rodeo-plan.yaml
    (iso_image, config_method, cluster_type, clu_rel, addons, sections)
    or via -P, e.g.  -P lab_in_a_box.iso_image=openSUSE-Leap-15.6.qcow2
    """
    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")

    cfg = load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)
    lab, warnings = build_lab_json(cfg, skip_unsupported=skip_unsupported)

    for warning in warnings:
        console.print(f"[yellow]⚠  {warning}[/yellow]")

    text = json.dumps(lab, indent=2) + "\n"
    if output == "-":
        click.echo(text, nl=False)
    else:
        Path(output).write_text(text)
        console.print(
            f"[green]✓  Wrote {output}[/green] — deploy with: setup_lab.sh {output}"
        )
