"""rodeo destroy — opt-in terminate of cloud KVM host (AWS)."""
from __future__ import annotations

import click
from rich.console import Console
from rich.prompt import Confirm

from ..config import ConfigError, load_config, validate_config
from ..providers.remote_up import destroy_primary

console = Console()


@click.command("destroy")
@click.option(
    "--cloud",
    "cloud",
    is_flag=True,
    help="Terminate the ownership-tagged cloud KVM host for this plan (AWS).",
)
@click.option("--yes", "-y", "assume_yes", is_flag=True, help="Skip confirmation.")
@click.option(
    "--config-dir",
    "config_dir",
    default=None,
    metavar="DIR",
    type=click.Path(file_okay=False, dir_okay=True, exists=False),
    help="Lab directory (default: auto-detect).",
)
def destroy_cmd(cloud: bool, assume_yes: bool, config_dir: str | None) -> None:
    """Destroy cloud infrastructure for a lab (does not clean nested VMs).

    Nested lab cleanup stays on the guest: SSH in and run ``rodeo clean``.
    This command only terminates the EC2 host tagged for the plan name.
    """
    if not cloud:
        console.print(
            "[red]✗  Refusing to run without --cloud "
            "(use 'rodeo clean' for nested VMs on the host).[/red]"
        )
        raise SystemExit(2)
    if not assume_yes and not Confirm.ask(
        "Terminate the AWS KVM host for this plan?",
        default=False,
    ):
        console.print("Aborted.")
        raise SystemExit(0)

    try:
        cfg = load_config(
            "rodeo-plan.yaml",
            config_dir=config_dir,
        )
        if cfg.get("deployment_target") != "aws":
            raise ConfigError(
                "rodeo destroy --cloud requires deployment_target: aws in the plan"
            )
        validate_config(cfg)
        results = destroy_primary(cfg)
    except ConfigError as exc:
        console.print(f"[red]✗  {exc}[/red]")
        raise SystemExit(1)

    for r in results:
        if r.ok:
            console.print(
                f"[green]✓[/green]  {r.id}  {r.provider_id or '—'}  "
                f"{r.detail or 'done'}"
            )
        else:
            console.print(f"[red]✗[/red]  {r.id}  {r.error or 'failed'}")
            raise SystemExit(1)
    console.print("[bold]Cloud host terminate requested.[/bold]")
