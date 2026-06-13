"""
bootstrap_cmd.py — "rodeo bootstrap" subcommand.

Purpose:
- Provides a clean, one-command path for operators after the minimal venv/pip setup.
- Handles creation of the /usr/local/bin/rodeo symlink (via install-deps --link)
  so that "rodeo" and "sudo rodeo" work as a normal global binary with no
  per-shell exports or long PATHs.
- Seeds a ready-to-use lab directory using the harvester-lab-config example
  (the 2-node no-Rancher variant tuned for modest hardware such as Ryzen 8c/16t).
- Prints copy-pasteable next steps that use the local lab dir as context
  (leveraging auto-detection of rodeo-plan.yaml when present).

This command is the programmatic counterpart to scripts/bootstrap-sles.sh.
It is intended for use after:
    python -m venv --system-site-packages .venv
    source .venv/bin/activate
    pip install -e ".[dev]"

It deliberately does not perform the initial clone or venv creation —
those remain explicit for development workflows. End users should prefer
the curl | bash bootstrap script for zero-manual-interaction setup on clean SLES.

Clean interface goals:
- After bootstrap, "rodeo" is a first-class command.
- Lab directories are self-describing (contain their own rodeo-plan.yaml + secrets.env).
- No mandatory --config-dir flags when operating inside a prepared lab dir.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console

console = Console()


@click.command("bootstrap")
@click.option(
    "--lab-dir",
    default=str(Path.home() / "harvester-rodeo-lab"),
    help="Target directory that will contain the seeded definition, plan, certs, and secrets.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Force re-initialization of the lab directory (overwrites existing rodeo-plan.yaml and secrets).",
)
def bootstrap_cmd(lab_dir: str, force: bool) -> None:
    """
    Prepare the host for clean `rodeo` invocation and seed a ready Harvester lab.

    Steps performed:
    1. Detect the active rodeo binary (from PATH or argv).
    2. If /usr/local/bin/rodeo does not exist, invoke `sudo rodeo install-deps --link`
       so that a stable global entry point is created. The symlink points at the
       venv's rodeo script (shebang preserves the correct Python + bindings).
    3. Create (or reuse) the lab directory.
    4. Run `rodeo init --example harvester-lab-config <lab>` (or with --force).
       This copies the full declarative example (definition.yaml, rodeo-plan.yaml,
       certs/, manifests/, helm/, custom/) and rewrites credentials to the ??env:
       form for easy `source rodeo-secrets.env + sudo -E` usage.
    5. Emit copy-pasteable follow-up commands that assume the lab dir as context.

    The resulting lab uses the 2-node no-Rancher configuration by default
    (see harvester-lab-config for details on CPU sizing for 8c/16t hosts).
    """
    # Locate the rodeo entry point that the user just activated / installed.
    # This is the one we will use for subsequent sudo and init invocations.
    rodeo_bin = shutil.which("rodeo") or sys.argv[0]
    if not rodeo_bin:
        console.print("[red]rodeo not found in PATH. Activate venv or run after pip install -e.[/red]")
        raise SystemExit(1)

    # Create (or update) the system-wide symlink for a clean UX.
    # This is the mechanism that removes the need for "export RODEO=..." and
    # long venv paths on every shell / sudo invocation.
    target = Path("/usr/local/bin/rodeo")
    needs_link = not (target.exists() or target.is_symlink())
    if needs_link:
        console.print("[bold]Linking rodeo binary for clean 'rodeo' / 'sudo rodeo' usage...[/bold]")
        try:
            # We deliberately pass the full path we discovered so sudo receives
            # a concrete executable even if /usr/local/bin is not yet in secure_path.
            subprocess.check_call(["sudo", rodeo_bin, "install-deps", "--link"])
        except subprocess.CalledProcessError:
            console.print(
                "[yellow]Link step failed or skipped. "
                "You can run 'sudo rodeo install-deps --link' manually later.[/yellow]"
            )

    # Prepare the lab directory. We resolve and mkdir early so that any
    # subsequent init or documentation refers to a concrete absolute path.
    lab = Path(lab_dir).expanduser().resolve()
    lab.mkdir(parents=True, exist_ok=True)

    # Delegate to the existing init machinery with the harvester-lab-config example.
    # This example is deliberately the "testing / modest hardware" variant
    # (2 nodes, 6 vCPU each, no Rancher) to keep first-time deploys feasible
    # on common developer / CI hosts.
    console.print(f"[bold]Initializing lab at {lab} with harvester-lab-config example...[/bold]")
    init_args = [rodeo_bin, "init", "--force" if force else "", "--example", "harvester-lab-config", str(lab)]
    # Filter empty strings that appear when --force is not used.
    init_args = [a for a in init_args if a]
    try:
        subprocess.check_call(init_args)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Init failed: {e}[/red]")
        raise SystemExit(1)

    # The init step always emits rodeo-secrets.env next to the plan.
    env_file = lab / "rodeo-secrets.env"

    # Emit the absolute minimal, copy-pasteable instructions.
    # Because the lab dir contains rodeo-plan.yaml, subsequent `rodeo plan`
    # and `rodeo deploy` invocations can omit --config-dir when the CWD is the lab.
    # sudo -E is still required to pass the ??env: variables.
    console.print("\n[bold green]Bootstrap complete.[/bold green]")
    console.print("Copy these commands:")
    console.print(f"  cd {lab}")
    if env_file.exists():
        console.print("  source rodeo-secrets.env")
    console.print("  rodeo plan --config-dir .")
    console.print("  sudo -E rodeo deploy --config-dir . --check")
    console.print(
        "\n(rodeo now uses /usr/local/bin link for clean invocation with no exports in most shells.)"
    )
