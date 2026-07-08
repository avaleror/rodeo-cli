"""rodeo self-update — pull latest code and reinstall the CLI in-place.

Hard guarantee: after a successful run the working tree is at the exact tip of
the remote's default branch (origin/main), or the command fails loudly. It will
never report success while leaving the host on stale code — the two failure
modes that used to allow that are both closed here:

  1. `git pull --ff-only` no-ops silently when the clone's upstream is stale,
     diverged, or on the wrong branch. We now fetch and hard-align to
     origin/<default-branch>, then assert HEAD == that tip.
  2. `from rodeo import __version__` in the running process returns the version
     loaded at start-up, not what pip just installed — so it always printed the
     OLD version. We now read the installed version in a fresh subprocess.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console

console = Console()

# Repo root is three levels up from this file:
# rodeo/commands/self_update_cmd.py → rodeo/commands/ → rodeo/ → <repo root>
_REPO_ROOT = Path(__file__).parent.parent.parent.resolve()
_VENV_PIP  = _REPO_ROOT / ".venv" / "bin" / "pip"


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in the repo, returning the completed process."""
    return subprocess.run(
        ["git", "-C", str(_REPO_ROOT), *args],
        capture_output=True, text=True, check=check,
    )


def _default_branch(remote: str) -> str:
    """Resolve the remote's default branch (e.g. 'main'), falling back to 'main'."""
    try:
        ref = _git("symbolic-ref", f"refs/remotes/{remote}/HEAD").stdout.strip()
        # refs/remotes/origin/main -> main
        if ref:
            return ref.rsplit("/", 1)[-1]
    except subprocess.CalledProcessError:
        pass
    # origin/HEAD may not be set on shallow/older clones — set it, then retry.
    try:
        _git("remote", "set-head", remote, "--auto", check=False)
        ref = _git("symbolic-ref", f"refs/remotes/{remote}/HEAD", check=False).stdout.strip()
        if ref:
            return ref.rsplit("/", 1)[-1]
    except subprocess.CalledProcessError:
        pass
    return "main"


def _installed_version() -> str:
    """Read the installed rodeo-cli version in a FRESH process.

    Must not use `rodeo.__version__` from the running process: that value was
    resolved at start-up and reflects the pre-update install, so it would report
    the old version even after a successful reinstall.
    """
    try:
        r = subprocess.run(
            [sys.executable, "-c",
             "import importlib.metadata as m; print(m.version('rodeo-cli'))"],
            capture_output=True, text=True, check=False,
        )
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _pyproject_version() -> str:
    """Version declared in the checked-out pyproject.toml (the source of truth)."""
    pyproject = _REPO_ROOT / "pyproject.toml"
    try:
        for line in pyproject.read_text().splitlines():
            s = line.strip()
            if s.startswith("version") and "=" in s:
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "unknown"


@click.command("self-update")
@click.option("--branch", default=None, metavar="NAME",
              help="Align to this branch instead of the remote default (main). For testing pre-release code.")
def self_update_cmd(branch: str | None) -> None:
    """Update rodeo-cli to the latest upstream code and reinstall the CLI.

    Fetches the remote and hard-aligns the working tree to the tip of the
    default branch (origin/main), then reinstalls. Fails loudly rather than
    leaving the host on stale code.
    """
    if not (_REPO_ROOT / ".git").exists():
        console.print(
            f"[red]✗  {_REPO_ROOT} is not a git repo — cannot self-update.[/red]\n"
            "Re-run the install script to get a fresh clone:\n"
            "  curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash"
        )
        raise SystemExit(1)

    remote = "origin"
    # Surface the origin URL — if this clone points at a stale fork, that is the
    # single most likely reason a host gets stranded, and it must be visible.
    try:
        origin_url = _git("remote", "get-url", remote).stdout.strip()
    except subprocess.CalledProcessError:
        console.print(f"[red]✗  no git remote '{remote}' configured — cannot self-update.[/red]")
        raise SystemExit(1)

    target_branch = branch or _default_branch(remote)
    target_ref = f"{remote}/{target_branch}"
    version_before = _installed_version()
    head_before = _git("rev-parse", "HEAD", check=False).stdout.strip()

    console.print(
        f"Updating rodeo-cli from [dim]{_REPO_ROOT}[/dim]\n"
        f"  remote : [dim]{origin_url}[/dim]\n"
        f"  target : [dim]{target_ref}[/dim]  (currently {version_before})"
    )

    # 1. Fetch the target with an EXPLICIT refspec so the remote-tracking ref
    #    (refs/remotes/origin/<branch>) is always written — a plain
    #    `git fetch origin main` leaves it stale on single-branch / custom-refspec
    #    clones, which is exactly how install.sh sets a host up. A genuinely
    #    missing branch (stale fork) fails here with "couldn't find remote ref".
    refspec = f"+refs/heads/{target_branch}:refs/remotes/{remote}/{target_branch}"
    fetch = _git("fetch", "--tags", "--prune", remote, refspec, check=False)
    if fetch.returncode != 0:
        err = fetch.stderr.strip()
        if "couldn't find remote ref" in err or "not found" in err.lower():
            console.print(
                f"[red]✗  branch '{target_branch}' does not exist on {remote}.[/red]\n"
                f"    The clone's origin ({origin_url}) may be a fork or stale mirror.\n"
                "    Re-run install.sh against avaleror/rodeo-cli to reset the remote."
            )
        else:
            console.print(f"[red]✗  git fetch {remote} {target_branch} failed:[/red]\n{err}")
        raise SystemExit(1)

    # 2. Resolve the tip we must land on. Prefer the tracking ref we just wrote;
    #    fall back to FETCH_HEAD (the just-fetched commit) if it isn't readable.
    tip = _git("rev-parse", target_ref, check=False)
    if tip.returncode != 0:
        tip = _git("rev-parse", "FETCH_HEAD", check=False)
    if tip.returncode != 0 or not tip.stdout.strip():
        console.print(
            f"[red]✗  could not resolve {target_ref} after fetch — aborting rather than "
            "risk leaving the host on stale code.[/red]"
        )
        raise SystemExit(1)
    target_sha = tip.stdout.strip()

    # 3. Hard-align the working tree to the remote tip. This is intentional: a
    #    self-updating host must end on exactly origin/<branch>, discarding any
    #    local drift that would otherwise block a fast-forward.
    if head_before and head_before != target_sha:
        dirty = _git("status", "--porcelain", check=False).stdout.strip()
        if dirty:
            console.print("[yellow]⚠  discarding local working-tree changes to align with the remote.[/yellow]")
    reset = _git("checkout", "-B", target_branch, target_sha, check=False)
    if reset.returncode != 0:
        console.print(f"[red]✗  could not check out {target_ref}:[/red]\n{reset.stderr.strip()}")
        raise SystemExit(1)
    _git("reset", "--hard", target_sha, check=False)

    # 4. Assert alignment BEFORE reinstalling — this is the anti-strand guarantee.
    head_after = _git("rev-parse", "HEAD", check=False).stdout.strip()
    if head_after != target_sha:
        console.print(
            f"[red]✗  working tree did not reach {target_ref} "
            f"(HEAD={head_after[:8]}, expected={target_sha[:8]}).[/red]\n"
            "    Refusing to report success on stale code."
        )
        raise SystemExit(1)

    # 5. Reinstall the package from the aligned tree.
    pip = str(_VENV_PIP) if _VENV_PIP.exists() else sys.executable.replace("rodeo", "pip")
    console.print("Reinstalling package...")
    r = subprocess.run([pip, "install", "--quiet", "-e", str(_REPO_ROOT)], capture_output=False)
    if r.returncode != 0:
        console.print("[red]✗  pip install failed — the CLI may be in a broken state.[/red]")
        raise SystemExit(r.returncode)

    # 6. Report the version read FRESH from disk, and sanity-check the install
    #    metadata matches the checked-out source (catches a partial pip install).
    version_after = _installed_version()
    source_version = _pyproject_version()
    if version_after != source_version and source_version != "unknown":
        console.print(
            f"[yellow]⚠  installed version {version_after} != source {source_version} — "
            "pip may not have refreshed metadata. Try: pip install -e . --force-reinstall[/yellow]"
        )

    if head_before == target_sha and version_before == version_after:
        console.print(f"\n[bold green]✓  Already up to date at {version_after}[/bold green] ({target_ref}).")
    else:
        console.print(
            f"\n[bold green]✓  rodeo-cli updated to {version_after}[/bold green] "
            f"(was {version_before}, now at {target_ref} {target_sha[:8]})."
        )
