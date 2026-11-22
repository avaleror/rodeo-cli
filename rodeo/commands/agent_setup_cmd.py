"""rodeo agent-setup: write the Claude Code kit (a mistake filter) into a lab."""
from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from .. import agent_kit
from ..config import ConfigError, find_lab_dir

console = Console()


def _target(directory: str | None) -> Path:
    if directory:
        return Path(directory).resolve()
    return find_lab_dir(use_last_lab=False) or Path.cwd().resolve()


@click.command("agent-setup")
@click.option("--dir", "directory", type=click.Path(file_okay=False, exists=True),
              help="Lab directory (default: the lab around the working directory, else it).")
@click.option("--check", is_flag=True, help="Only report whether the kit is installed (exit 1 if not).")
def agent_setup_cmd(directory: str | None, check: bool) -> None:
    """Write Claude Code settings that keep an agent on the rodeo MCP tools.

    Merges into <lab>/.claude/settings.json: deny rules for `rodeo`, `sudo`,
    `virsh` and ssh/scp to the lab's hosts in the agent's shell, and a
    PreToolUse hook that points the model at the MCP tools. Your own rules and
    hooks are kept, and running it again changes nothing.

    This is a mistake filter, not a security boundary: it is a settings file
    the same user can edit, so it stops a confused model, not a hostile one.
    MCP local deploys still need NOPASSWD sudo; the kit only denies sudo in the
    agent's shell. Only Claude Code has a kit today.
    """
    lab = _target(directory)
    if check:
        st = agent_kit.status(lab)
        if st["claude_code"]:
            console.print(f"[green]✓[/green]  Claude Code kit installed: {st['settings']}")
            return
        console.print(f"[yellow]⚠[/yellow]  No Claude Code kit in {lab}"
                      + (f" ({st['problem']})" if st["problem"] else ""))
        raise SystemExit(1)
    try:
        path, changed = agent_kit.install(lab)
    except ConfigError as exc:
        raise click.ClickException(str(exc))
    hosts = agent_kit.lab_hosts(lab)
    if changed:
        console.print(f"[green]✓[/green]  Wrote the Claude Code kit to {path}")
    else:
        console.print(f"[green]✓[/green]  Claude Code kit already up to date in {path}")
    console.print("   Denied in the agent's shell: rodeo, sudo, virsh"
                  + (f", ssh/scp to {', '.join(hosts)}" if hosts else ""))
    console.print(
        "   [dim]A mistake filter, not a boundary: the same user can edit this file.\n"
        "   MCP local deploys (rodeo_up, rodeo_deploy) still need NOPASSWD sudo.[/dim]"
    )
