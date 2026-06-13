"""rodeo — SUSE Virtualization Rodeo CLI entry point."""
from __future__ import annotations

import click
from rich.console import Console

from . import __version__
from .commands.attach import attach_cmd
from .commands.clean import clean_cmd
from .commands.deploy import deploy_cmd
from .commands.init_cmd import init_cmd
from .commands.install_deps import install_deps_cmd
from .commands.bootstrap_cmd import bootstrap_cmd
from .commands.generate_cmd import generate_cmd
from .commands.logs import logs_cmd
from .commands.plan_cmd import plan_cmd
from .commands.restart import restart_cmd
from .commands.ssh_cmd import ssh_cmd
from .commands.status import status_cmd
from .commands.stop_cmd import stop_cmd
from .commands.start_cmd import start_cmd
from .commands.watch import watch_cmd
from .config import ConfigError


class _RodeoGroup(click.Group):
    """Turn ConfigError into a clean message instead of a traceback."""

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except ConfigError as exc:
            Console(stderr=True).print(f"[red]✗  {exc}[/red]")
            ctx.exit(1)


@click.group(cls=_RodeoGroup, context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version")
@click.option(
    "--config-dir", "config_dir", default=None, metavar="DIR",
    type=click.Path(file_okay=False, dir_okay=True, exists=False),
    help="Config directory for EIB-style declarative setup (definition.yaml + artifacts). "
         "Option can appear before or after the subcommand.",
)
def cli(config_dir: str | None) -> None:
    """Deploy and manage the SUSE Virtualization Rodeo cluster.

    \b
    Quick start:
      # Minimal: one curl (no manual clone/venv/pip)
      curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/scripts/bootstrap-sles.sh | bash
      # or init with profile: rodeo init --profile test  (2 small nodes) or --profile harvester (full 3+1)
      # then follow printed (cd lab, source, plan, deploy)
      # Lifecycle: stop/start for graceful (infra-aware per def); clean --all --hard for reset (stop first unless --hard)
      rodeo status
    """
    # Store for subcommands that don't get it from their own decorator
    ctx = click.get_current_context()
    ctx.obj = ctx.obj or {}
    ctx.obj["config_dir"] = config_dir


cli.add_command(install_deps_cmd, name="install-deps")
cli.add_command(bootstrap_cmd,    name="bootstrap")
cli.add_command(generate_cmd,     name="generate")
cli.add_command(init_cmd,         name="init")
cli.add_command(plan_cmd,         name="plan")
cli.add_command(deploy_cmd,       name="deploy")
cli.add_command(clean_cmd,        name="clean")
cli.add_command(stop_cmd,         name="stop")
cli.add_command(start_cmd,        name="start")
cli.add_command(status_cmd,       name="status")
cli.add_command(watch_cmd,        name="watch")
cli.add_command(restart_cmd,      name="restart")
cli.add_command(ssh_cmd,          name="ssh")
cli.add_command(logs_cmd,         name="logs")
cli.add_command(attach_cmd,       name="attach")
