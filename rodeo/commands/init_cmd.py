"""rodeo init — scaffold a rodeo-plan.yaml and ~/.rodeo/secrets.yaml."""
from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path

import click
from rich.console import Console

from ..secretgen import random_password as _random_password
from ..secretgen import read_secrets_file, write_secrets_file

console = Console()

_TEMPLATES = Path(__file__).parent.parent / "data" / "templates"


def _pick_password(ask: bool) -> tuple[str, str]:
    """Return (password, source). Precedence: --ask > $RODEO_PASSWORD > random.

    Never accept the password as a CLI argument — it would land in shell
    history and process listings.
    """
    if ask:
        pw = click.prompt(
            "Lab password (12+ chars, Rancher requires it)",
            hide_input=True, confirmation_prompt=True,
        )
        if len(pw) < 12:
            console.print("[red]✗  Password must be at least 12 characters (Rancher minimum).[/red]")
            raise SystemExit(1)
        return pw, "from prompt"

    env_pw = os.environ.get("RODEO_PASSWORD", "")
    if env_pw:
        if len(env_pw) < 12:
            console.print("[red]✗  $RODEO_PASSWORD must be at least 12 characters.[/red]")
            raise SystemExit(1)
        return env_pw, "from $RODEO_PASSWORD"

    return _random_password(), "random"


@click.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing files.")
@click.option("--ask", "ask_password", is_flag=True,
              help="Prompt for the lab password instead of generating one.")
@click.option("--profile", "profile", default=None, metavar="NAME",
              help="Seed using a bundled profile: 'rancher' (1 VM, no Harvester), 'test' (2-node Harvester, "
                   "no Rancher), 'harvester-ha' (3-node Harvester, no Rancher), or 'harvester' (3-node + Rancher). "
                   "Copies definition + plan + artifacts.")
@click.option("--example", "example", default=None, metavar="NAME", hidden=True,
              help="Legacy. Use --profile instead.")
@click.argument("target_dir", default=".", type=click.Path())
def init_cmd(force: bool, ask_password: bool, target_dir: str, profile: str | None = None, example: str | None = None) -> None:
    """Generate rodeo-plan.yaml and ~/.rodeo/secrets.yaml.

    \b
    Password source (first match wins):
      1. --ask            interactive hidden prompt
      2. $RODEO_PASSWORD  environment variable (CI / Instruqt setup scripts)
      3. generated        random 16 characters

    With --profile (test or harvester) you get a ready-to-use lab dir (including EIB-style artifacts) in one step.
    """
    dest = Path(target_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    plan_dest = dest / "rodeo-plan.yaml"
    from ..paths import rodeo_secrets_path

    secrets_dest = rodeo_secrets_path()

    if profile:
        from ..labseed import PROFILE_EXAMPLE
        example = PROFILE_EXAMPLE.get(profile)
        if example is None:
            console.print(f"[red]Unknown profile '{profile}'. Use one of: {', '.join(PROFILE_EXAMPLE)}.[/red]")
            raise SystemExit(1)
    if example:
        src = Path(__file__).parent.parent / "data" / "examples" / example
        if not src.is_dir():
            console.print(f"[red]✗  Example directory not found: {src}[/red]")
            raise SystemExit(1)
        console.print(f"[bold]  Seeding from example '{example}' into {dest}...[/bold]")
        for item in src.iterdir():
            dst_item = dest / item.name
            try:
                if item.is_dir():
                    if dst_item.exists():
                        if force:
                            shutil.rmtree(dst_item)
                            shutil.copytree(item, dst_item)
                            console.print(f"[green]✓[/green]  replaced dir {item.name}")
                        else:
                            console.print(f"[yellow]  {dst_item} exists — skipping (use --force)[/yellow]")
                    else:
                        shutil.copytree(item, dst_item)
                        console.print(f"[green]✓[/green]  copied dir {item.name}")
                else:
                    if dst_item.exists() and not force:
                        console.print(f"[yellow]  {dst_item} exists — skipping (use --force)[/yellow]")
                    else:
                        shutil.copy2(item, dst_item)
                        console.print(f"[green]✓[/green]  copied {item.name}")
            except Exception as exc:
                console.print(f"[yellow]  ⚠ could not copy {item.name}: {exc}[/yellow]")

    # Plan handling: never blindly overwrite a seeded example plan with the generic template.
    if plan_dest.exists() and not force and not example:
        console.print(f"[yellow]{plan_dest} already exists — use --force to overwrite.[/yellow]")
    else:
        if not plan_dest.exists() and not example:
            shutil.copy(_TEMPLATES / "rodeo-plan.yaml", plan_dest)
        if plan_dest.exists():
            console.print(f"[green]✓[/green]  {plan_dest}")

    # Secrets + env file (robust: always produce a fresh rodeo-secrets.env even on re-init without --force)
    secrets_dest.parent.mkdir(parents=True, exist_ok=True)
    password = token = None
    if secrets_dest.exists() and not force:
        # re-use values from existing secrets so we can still emit a fresh .env without overwriting the 600 file
        password, token = read_secrets_file(secrets_dest)
        if password and token:
            console.print(f"[yellow]{secrets_dest} already exists — will use its values for a fresh {dest / 'rodeo-secrets.env'}[/yellow]")
        else:
            console.print(f"[yellow]{secrets_dest} exists but could not parse values — regenerating[/yellow]")

    if not (password and token):
        # (re)generate secrets only when we are allowed to (first time or --force).
        # If we couldn't parse an existing secrets (no --force), fall back to generating fresh values
        # and write the secrets file anyway — the .env is useless without them, and --force is the common test path.
        password, source = _pick_password(ask_password)
        token = secrets.token_urlsafe(24)
        write_secrets_file(secrets_dest, password, token)
        console.print(
            f"[green]✓[/green]  {secrets_dest}  [dim](chmod 600, password: {source})[/dim]"
        )

    # Generate the sourceable env file for CI / advanced use.
    env_file = dest / "rodeo-secrets.env"
    env_content = (
        f'export HARVESTER_OS_PASSWORD="{password}"\n'
        f'export HARVESTER_ADMIN_PASSWORD="{password}"\n'
        f'export RANCHER_ADMIN_PASSWORD="{password}"\n'
        f'export RANCHER_VM_PASSWORD="{password}"\n'
        f'export HARVESTER_TOKEN="{token}"\n'
    )
    env_file.write_text(env_content)

    console.print("\n[bold]Next:[/bold]")
    console.print(f"  rodeo up --dir {dest}     # recommended: handles sudo + preflight")
    console.print(f"  rodeo deploy --no-tui --config-dir {dest}  # or deploy directly")
