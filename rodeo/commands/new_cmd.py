"""rodeo new — scaffold a custom, editable rodeo you can run with 'rodeo up'.

The declarative authoring loop:

  rodeo new mylab --from harvester     # copy a working lab into ~/.rodeo/profiles/mylab
  $EDITOR ~/.rodeo/profiles/mylab/definition.yaml   # change nodes, network, resources
  rodeo up --profile mylab             # deploy your edited lab

A profile is just a config-dir: a declarative ``definition.yaml`` (the topology) plus
a ``rodeo-plan.yaml`` (type, resources, credentials). See docs/custom-rodeos.md.

``rodeo new mylab --from-zip mylab.zip`` installs a rodeo downloaded from the Rodeo
Builder: its files laid over the base profile its ``builder.yaml`` names (or --from).
``rodeo new mylab --from https://github.com/<owner>/<repo>[@<ref>]`` does the same with
a published rodeo's repository (see :mod:`rodeo.catalog`).
``rodeo new mylab --from-lab-json lab.json`` makes a lab-in-a-box rodeo that deploys
that lab-in-a-box lab as written (see :mod:`rodeo.labimport`).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import click
from rich.console import Console

from .. import catalog, labimport
from ..labseed import PROFILE_EXAMPLE, scaffold_profile
from ..profile_install import install_profile, manifest_base, read_zip

console = Console()


@click.command("new",
               short_help="Scaffold a custom rodeo you can edit, then 'rodeo up --profile <name>'.")
@click.argument("name")
@click.option("--from", "from_base", default=None,
              help="Working lab to copy as the starting point (default: harvester; with --from-zip, "
                   "the base named in the zip's builder.yaml), or a published rodeo's repository "
                   "https://<host>/<owner>/<repo>[@<ref>]. Bundled: " + ", ".join(PROFILE_EXAMPLE) + ".")
@click.option("--from-zip", "from_zip", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Install a rodeo downloaded from the Rodeo Builder.")
@click.option("--from-lab-json", "from_lab", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Make a lab-in-a-box rodeo from a lab-in-a-box lab (lab.json, or the lab-builder's YAML).")
@click.option("--force", is_flag=True, help="Overwrite an existing custom profile of this name.")
def new_cmd(name: str, from_base: str | None, from_zip: Path | None, from_lab: Path | None, force: bool) -> None:
    """Create an editable custom rodeo named NAME under ~/.rodeo/profiles/."""
    if from_lab:
        _new_from_lab(name, from_lab, from_base, from_zip, force)
        return
    try:
        if from_base and from_base.startswith("https://") and not from_zip:
            repo, ref = catalog.parse_spec(from_base)
            dest = catalog.install(name, repo, ref, force=force)
            warning = catalog.language_warning(dest, repo, ref)
            if warning:
                console.print(f"[yellow]⚠  {warning}[/yellow]", highlight=False)
        elif from_zip:
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
    except (FileExistsError, FileNotFoundError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
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


def _new_from_lab(name: str, path: Path, from_base: str | None, from_zip: Path | None, force: bool) -> None:
    """rodeo new NAME --from-lab-json PATH: install the lab's rodeo and say what changed on import."""
    if from_base or from_zip:
        console.print("[red]✗  --from-lab-json makes its own lab-in-a-box rodeo: drop --from / --from-zip[/red]")
        raise SystemExit(1)
    try:
        files, imported = labimport.profile_files(name, labimport.read_lab(path))
        dest = install_profile(name, files, force=force)
    except (FileExistsError, ValueError, OSError) as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    console.print(f"\n[bold green]Created profile '{name}'[/bold green] (lab-in-a-box lab from '{path.name}') at:")
    console.print(f"  [cyan]{dest}[/cyan]\n")
    console.print(f"The lab is lab_in_a_box.lab in {dest / 'rodeo-plan.yaml'}: rodeo deploys it as written,")
    console.print("with this host's network, domain, node addresses and secrets.\n")
    if imported.replaced:
        console.print("[bold]Credentials replaced by ??placeholders[/bold] (operator_secrets, asked once by rodeo up):")
        console.print("  " + ", ".join(imported.replaced) + "\n")
    if imported.warnings:
        console.print("[bold yellow]Check:[/bold yellow]")
        for w in imported.warnings:
            console.print(f"  [yellow]•[/yellow] {w}")
        console.print()
    console.print("[bold]Then deploy it:[/bold]")
    console.print(f"  rodeo up --profile {name}\n")
