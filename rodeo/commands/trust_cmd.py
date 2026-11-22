"""rodeo trust: approve a lab so write commands may resolve its secrets.

    rodeo trust            # review what this lab will do, then approve it
    rodeo trust --yes      # approve without asking (Instruqt setup, CI)
    rodeo trust --list     # trusted labs
    rodeo trust --revoke   # forget this lab
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.markup import escape

from ..config import (
    SOURCE_PREFIX,
    ConfigError,
    is_lab_trusted,
    is_secret_source,
    collect_placeholders,
    lab_dir_of,
    load_config,
    _hashed_files,
    _walk_lab,
    lab_fingerprint,
    lab_problems,
    load_trusted_labs,
    revoke_lab,
    trust_lab,
)
from ..paths import rodeo_trusted_sources_path
from ._options import config_options

console = Console()


def _interactive() -> bool:
    return sys.stdin.isatty()


def _warn_obsolete_file() -> None:
    old = rodeo_trusted_sources_path()
    if old.exists() or old.is_symlink():
        console.print(
            f"[yellow]⚠  {old} is obsolete and ignored: trust is per lab now. "
            "You can delete it.[/yellow]"
        )


def _hashed(lab: Path) -> list[str]:
    return [str(p.relative_to(lab)) for p in _hashed_files(lab)]


def _summary(cfg: dict, lab: Path) -> list[str]:
    """What approving this lab lets rodeo do, for the operator to review."""
    lines = [f"Profile type: {cfg.get('type', '?')}"]
    sources = sorted(s for s in collect_placeholders(cfg) if is_secret_source(s))
    lines.append("Secret sources (??env: reads a variable, ??file: a file, ??cmd: runs as you or root):")
    lines += [f"  {s}" for s in sources] or ["  none"]
    scripts = sorted(
        str(p.relative_to(lab)) for p in (lab / "custom").rglob("*") if p.is_file()
    ) if (lab / "custom").is_dir() else []
    lines.append("Custom files (custom/scripts run as root on deploy):")
    lines += [f"  {s}" for s in scripts] or ["  none"]
    content = cfg.get("content")
    repos = []
    if isinstance(content, dict) and content.get("repo"):
        repos = [f"{content['repo']} (ref {content.get('ref') or 'default branch'})"]
    ws = cfg.get("workshop")
    if isinstance(ws, dict) and ws.get("repo"):
        repos.append(f"{ws['repo']} (workshop, branch {ws.get('branch', 'main')})")
    lines.append("Content repositories, trusted by URL and ref, not by commit: a branch can change")
    lines.append("after you approve, and their scripts are not reviewed here:")
    lines += [f"  {r}" for r in repos] or ["  none"]
    links = [str(p.relative_to(lab)) for p in _walk_lab(lab)[1]]
    if links:
        lines.append("Symlinks (their targets, inside the lab, are covered):")
        lines += [f"  {link} -> {os.readlink(lab / link)}" for link in links]
    others = [p for p in _hashed(lab) if not p.startswith("custom/")]
    lines.append(f"Other files covered by this approval: {len(others)} (plan, definition, manifests, ...)")
    return lines


@click.command("trust")
@config_options
@click.option("--yes", "assume_yes", is_flag=True,
              help="Approve without asking: for automation (Instruqt setup scripts, CI).")
@click.option("--list", "list_only", is_flag=True, help="Show the trusted labs and exit.")
@click.option("--revoke", is_flag=True, help="Forget this lab's approval.")
def trust_cmd(
    config_path: str,
    config_dir: str | None,
    params: tuple[str, ...],
    paramfile: str | None,
    assume_yes: bool,
    list_only: bool,
    revoke: bool,
) -> None:
    """Approve this lab so deploy and other write commands may resolve its secrets.

    A lab runs its own code on deploy (custom/scripts, content scripts, ??cmd:
    sources), so rodeo trusts whole labs, not single strings. The approval is a
    sha256 over the lab directory (plan, definition.yaml, custom/, manifests/,
    helm/, certs/, story/...; not the state rodeo writes there): when a file
    changes, the lab is untrusted again until you re-run rodeo trust. Labs rodeo
    seeds into an empty directory from its bundled profiles are trusted already.
    Content repositories fetched by URL are trusted by URL and ref, not commit.

    The hash is checked when a write command loads the lab; a long deploy reads
    custom scripts and manifests later. A file changed during that window is
    used as changed, so only edit a lab while nothing is deploying it.

    Read commands (plan, status, ...) never resolve secrets. -P and --paramfile
    are ignored here, and may not carry `??` anywhere.

    Approvals live in ~/.rodeo/trusted-labs.yaml. That file is ordinary user
    data: any process running as you can edit it. Without --yes this command
    needs an interactive terminal, which only stops MCP clients and other
    non-interactive callers. rodeo's MCP server does not offer this command.
    """
    _warn_obsolete_file()
    try:
        if list_only:
            labs = load_trusted_labs()
            if not labs:
                console.print("No trusted labs.")
            for lab, digest in sorted(labs.items()):
                if lab.startswith(SOURCE_PREFIX):
                    console.print(f"{escape(lab[len(SOURCE_PREFIX):])}  [dim]fleet lab.source (URL@branch)[/dim]")
                    continue
                state = "" if is_lab_trusted(lab) else "  [yellow](changed since approval)[/yellow]"
                console.print(f"{escape(lab)}  [dim]{digest[:12]}[/dim]{state}")
            return

        if config_dir is None:
            ctx = click.get_current_context()
            if ctx.obj:
                config_dir = ctx.obj.get("config_dir")
        if params or paramfile:
            console.print("[dim]Ignoring -P/--paramfile: only the lab's own files are approved.[/dim]")
        cfg = load_config(config_path, config_dir=config_dir)
        lab_str = lab_dir_of(cfg)
        if lab_str is None:
            raise click.ClickException("No rodeo-plan.yaml found: run rodeo trust inside a lab.")
        lab = Path(lab_str)

        if revoke:
            if not (assume_yes or _interactive()):
                raise click.ClickException("rodeo trust --revoke needs an interactive terminal or --yes.")
            if revoke_lab(lab):
                console.print(f"Revoked trust for {escape(lab_str)}.")
            else:
                console.print(f"{escape(lab_str)} was not trusted.")
            return

        if cfg.get("_lab_split"):
            raise click.ClickException(
                "This lab's plan is a symlink or lives outside --config-dir: put the plan file "
                "itself in the lab directory, then run rodeo trust."
            )
        problems = lab_problems(lab)
        if problems:
            raise click.ClickException("This lab can't be trusted as it is:\n  " + "\n  ".join(problems))
        if is_lab_trusted(lab):
            console.print(f"{escape(lab_str)} is already trusted.")
            return
        # Hash first: the approval is for exactly the files summarised below.
        digest = lab_fingerprint(lab)
        console.print(f"[bold]Lab {escape(lab_str)}[/bold]\n")
        for line in _summary(cfg, lab):
            console.print(escape(line), highlight=False)
        console.print(
            "\n[yellow]Approve only a lab you have reviewed. Deploy runs its scripts as root.[/yellow]"
        )
        if not assume_yes:
            if not _interactive():
                raise click.ClickException(
                    "rodeo trust needs an interactive terminal (or --yes for automation)."
                )
            if not click.confirm("Trust this lab?", default=False):
                raise click.ClickException("Not trusted.")
        path = trust_lab(lab, digest)
    except (ConfigError, yaml.YAMLError) as exc:
        raise click.ClickException(str(exc))
    console.print(f"Trusted {escape(lab_str)} ({path}).")
