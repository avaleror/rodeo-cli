"""rodeo new — scaffold a custom, editable rodeo you can run with 'rodeo up'.

The declarative authoring loop:

  rodeo new mylab --from harvester     # copy a working lab into ~/.rodeo/profiles/mylab
  $EDITOR ~/.rodeo/profiles/mylab/definition.yaml   # change nodes, network, resources
  rodeo up --profile mylab             # deploy your edited lab

A profile is just a config-dir: a declarative ``definition.yaml`` (the topology) plus
a ``rodeo-plan.yaml`` (type, resources, credentials). See docs/custom-rodeos.md.

``rodeo new mylab --from-zip mylab.zip`` installs a rodeo downloaded from the Rodeo
Builder: its files laid over the base profile its ``builder.yaml`` names (or --from).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import click
from rich.console import Console

from ..labseed import PROFILE_EXAMPLE, scaffold_profile
from ..profile_install import install_profile, manifest_base, read_zip

console = Console()


@click.command("new",
               short_help="Scaffold a custom rodeo you can edit, then 'rodeo up --profile <name>'.")
@click.argument("name")
@click.option("--from", "from_base", default=None,
              help="Working lab to copy as the starting point (default: harvester; with --from-zip, "
                   "the base named in the zip's builder.yaml). Bundled: " + ", ".join(PROFILE_EXAMPLE) + ".")
@click.option("--from-zip", "from_zip", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Install a rodeo downloaded from the Rodeo Builder.")
@click.option("--force", is_flag=True, help="Overwrite an existing custom profile of this name.")
def new_cmd(name: str, from_base: str | None, from_zip: Path | None, force: bool) -> None:
    """Create an editable custom rodeo named NAME under ~/.rodeo/profiles/."""
    try:
        if from_zip:
            files = read_zip(from_zip)
            from_base = from_base or manifest_base(files)
            dest = install_profile(name, files, base=from_base, force=force)
        else:
            if from_base is None:
                from_base = "harvester"
            if from_base not in PROFILE_EXAMPLE:
                raise FileNotFoundError(
                    f"--from '{from_base}' is not a bundled base. Use one of: {', '.join(PROFILE_EXAMPLE)}")
            dest = scaffold_profile(name, from_base=from_base, force=force)
    except (FileExistsError, FileNotFoundError, ValueError, zipfile.BadZipFile) as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    origin = f"from '{from_zip.name}'" + (f" on '{from_base}'" if from_base else "") if from_zip else f"from '{from_base}'"
    console.print(f"\n[bold green]Created profile '{name}'[/bold green] ({origin}) at:")
    console.print(f"  [cyan]{dest}[/cyan]\n")
    console.print("[bold]Edit the topology[/bold] (nodes, network, resources, exposed services):")
    console.print(f"  {dest / 'definition.yaml'}")
    console.print(f"  {dest / 'rodeo-plan.yaml'}\n")
    console.print("[bold]Then deploy it:[/bold]")
    console.print(f"  rodeo up --profile {name}\n")
    console.print("[dim]Format and how-to: docs/custom-rodeos.md · List all: rodeo profiles[/dim]")
