"""Shared CLI options for commands that load the plan."""
from __future__ import annotations

import click


def config_options(f):
    """--config / -P / --paramfile, in reverse order so help reads naturally."""
    f = click.option(
        "--paramfile", default=None, metavar="FILE",
        help="YAML file of overrides, deep-merged over the plan (like tfvars).",
    )(f)
    f = click.option(
        "-P", "--param", "params", multiple=True, metavar="KEY=VALUE",
        help="Override a plan value by dotted path "
             "(e.g. -P resources.harvester.memory_mib=20480) "
             "and/or feed a template parameter. Repeatable.",
    )(f)
    f = click.option(
        "--config", "config_path", default="rodeo-plan.yaml", show_default=True,
    )(f)
    return f
