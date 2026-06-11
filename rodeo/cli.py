"""rodeo — SUSE Virtualization Rodeo CLI entry point."""
from __future__ import annotations

import click

from . import __version__
from .commands.attach import attach_cmd
from .commands.clean import clean_cmd
from .commands.deploy import deploy_cmd
from .commands.init_cmd import init_cmd
from .commands.install_deps import install_deps_cmd
from .commands.logs import logs_cmd
from .commands.restart import restart_cmd
from .commands.ssh_cmd import ssh_cmd
from .commands.status import status_cmd
from .commands.watch import watch_cmd


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version")
def cli() -> None:
    """Deploy and manage the SUSE Virtualization Rodeo cluster.

    \b
    Quick start:
      sudo rodeo install-deps   # once, on a fresh host
      rodeo init                # generate rodeo-plan.yaml
      rodeo deploy              # run the full pipeline
      rodeo status              # check cluster health
    """


cli.add_command(install_deps_cmd, name="install-deps")
cli.add_command(init_cmd,         name="init")
cli.add_command(deploy_cmd,       name="deploy")
cli.add_command(clean_cmd,        name="clean")
cli.add_command(status_cmd,       name="status")
cli.add_command(watch_cmd,        name="watch")
cli.add_command(restart_cmd,      name="restart")
cli.add_command(ssh_cmd,          name="ssh")
cli.add_command(logs_cmd,         name="logs")
cli.add_command(attach_cmd,       name="attach")
