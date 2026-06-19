"""rodeo init — scaffold a rodeo-plan.yaml and ~/.rodeo/secrets.yaml."""
from __future__ import annotations

import os
import secrets
import shutil
import stat
from pathlib import Path

import click
from rich.console import Console

from ..secretgen import random_password as _random_password

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
    secrets_dest = Path.home() / ".rodeo" / "secrets.yaml"

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
    # Always ensure the three credential lines use the ??env: form (so source rodeo-secrets.env + sudo -E works).
    if plan_dest.exists() and not force and not example:
        console.print(f"[yellow]{plan_dest} already exists — use --force to overwrite.[/yellow]")
    else:
        if not plan_dest.exists() and not example:
            # only fall back to generic template when nothing provided a plan
            shutil.copy(_TEMPLATES / "rodeo-plan.yaml", plan_dest)
        # Rewrite (or ensure) env form on whatever plan we have now (template, example, or pre-existing under force)
        if plan_dest.exists():
            plan_text = plan_dest.read_text()
            orig_text = plan_text
            plan_text = plan_text.replace(
                'harvester_os_password: "??harvester_os_password"',
                'harvester_os_password: "??env:HARVESTER_OS_PASSWORD"'
            )
            plan_text = plan_text.replace(
                'lab_admin_password: "??lab_admin_password"',
                'lab_admin_password: "??env:LAB_ADMIN_PASSWORD"'
            )
            plan_text = plan_text.replace(
                'harvester_token: "??harvester_token"',
                'harvester_token: "??env:HARVESTER_TOKEN"'
            )
            if plan_text != orig_text:
                plan_dest.write_text(plan_text)
                console.print(f"[green]✓[/green]  {plan_dest}  [dim](env-var ready)]")
            else:
                console.print(f"[green]✓[/green]  {plan_dest}  [dim](already env-var ready)]")

    # Secrets + env file (robust: always produce a fresh rodeo-secrets.env even on re-init without --force)
    secrets_dest.parent.mkdir(parents=True, exist_ok=True)
    password = None
    token = None
    if secrets_dest.exists() and not force:
        # re-use values from existing secrets so we can still emit a fresh .env without overwriting the 600 file
        try:
            for line in secrets_dest.read_text().splitlines():
                if line.startswith("harvester_os_password:"):
                    password = line.split(":", 1)[1].strip().strip('"\'')
                elif line.startswith("harvester_token:"):
                    token = line.split(":", 1)[1].strip().strip('"\'')
        except Exception:
            pass
        if password and token:
            console.print(f"[yellow]{secrets_dest} already exists — will use its values for a fresh {dest / 'rodeo-secrets.env'}[/yellow]")
        else:
            console.print(f"[yellow]{secrets_dest} exists but could not parse values — regenerating[/yellow]")

    if not (password and token):
        # (re)generate secrets only when we are allowed to (first time or --force).
        # If we couldn't parse an existing secrets (no --force), fall back to generating fresh values
        # and write the secrets file anyway — the .env is useless without them, and --force is the common test path.
        secrets_dest.parent.mkdir(parents=True, exist_ok=True)
        password, source = _pick_password(ask_password)
        token = secrets.token_urlsafe(24)
        secrets_dest.write_text(
            "# ~/.rodeo/secrets.yaml — kept out of version control\n"
            "# Generated by rodeo init.\n"
            "#\n"
            "# harvester_os_password   — OS console (rancher user SSH/TTY)\n"
            "# harvester_admin_password — Harvester web UI admin account\n"
            "# rancher_admin_password   — Rancher web UI admin account\n"
            "# harvester_token          — cluster join token (shared by all nodes)\n"
            f'harvester_os_password: "{password}"\n'
            f'harvester_admin_password: "{password}"\n'
            f'rancher_admin_password: "{password}"\n'
            f'lab_admin_password: "{password}"\n'
            f'harvester_token: "{token}"\n'
        )
        secrets_dest.chmod(stat.S_IRUSR | stat.S_IWUSR)  # chmod 600
        console.print(
            f"[green]✓[/green]  {secrets_dest}  [dim](chmod 600, password: {source})[/dim]"
        )

    # Always (re)generate the sourceable env file next to the plan. Safe and what makes sudo -E flows easy.
    env_file = dest / "rodeo-secrets.env"
    env_content = (
        f'export HARVESTER_OS_PASSWORD="{password}"\n'
        f'export LAB_ADMIN_PASSWORD="{password}"\n'
        f'export HARVESTER_TOKEN="{token}"\n'
    )
    env_file.write_text(env_content)
    console.print(f"[green]✓[/green]  {env_file}  [dim](source this for env-var mode)]")

    # Richer, copy-paste friendly next steps. Helps cut the manual export / sudo -E dance.
    rodeo_hint = os.environ.get("RODEO") or shutil.which("rodeo") or "$(pwd)/.venv/bin/rodeo"
    console.print("\n[bold]Next steps:[/bold]")
    console.print("  # Ensure you have a convenient RODEO var for sudo (sudo does not inherit PATH):")
    console.print(f"  export RODEO={rodeo_hint}")
    console.print(f"  source {env_file.name}                    # load the passwords into current shell")
    console.print("  sudo -E $RODEO deploy --check            # preflight (uses the ??env: values)")
    if example:
        console.print("")
        console.print("  # Your dir was seeded with the example — use --config-dir (or cd here and omit it):")
        console.print("  $RODEO plan --config-dir .")
        console.print("  sudo -E $RODEO deploy --config-dir .")
    console.print(
        "\nWhy the source + sudo -E pattern?"
    )
    console.print(
        "A child process cannot mutate the parent's environment. Sourcing the generated .env + sudo -E is the"
    )
    console.print(
        "least-surprise way that works for interactive tests, different shells, and CI without copying secrets to /root."
    )
