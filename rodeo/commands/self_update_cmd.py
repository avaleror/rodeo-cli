"""rodeo self-update — move to the latest validated release and reinstall in place.

By default the target is the tag in STABLE on main: the last release that
passed a live deploy (docs/releasing.md). --branch NAME follows a branch tip
instead (--branch main for the latest code).

Hard guarantee: after a successful run the working tree is exactly at the
target (that tag, or the tip of origin/<branch>), or the command fails loudly. It will
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

import re
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console

from ..install_source import default_install_url, repo_url

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


_TAG_RE = re.compile(r"v\d+\.\d+\.\d+")


def _stable_tag(remote: str) -> str | None:
    """The validated release to install: STABLE on <remote>/main, else the newest tag.

    Call after fetching main and the tags. None when neither exists.
    """
    shown = _git("show", f"{remote}/main:STABLE", check=False)
    tag = shown.stdout.strip() if shown.returncode == 0 else ""
    if _TAG_RE.fullmatch(tag) and _git("rev-parse", "-q", "--verify", f"refs/tags/{tag}",
                                       check=False).returncode == 0:
        return tag
    tags = _git("tag", "-l", "v[0-9]*", "--sort=-v:refname", check=False).stdout.split()
    newest = next((t for t in tags if _TAG_RE.fullmatch(t)), None)
    if newest:
        console.print(f"[yellow]⚠  no valid STABLE file on {remote}/main: using the newest release, {newest}.[/yellow]")
    return newest


def _fetch_failed(err: str, remote: str, branch: str, origin_url: str) -> None:
    """Explain a failed fetch of <remote>/<branch> and exit 1."""
    if "couldn't find remote ref" in err or "not found" in err.lower():
        console.print(
            f"[red]✗  branch '{branch}' does not exist on {remote}.[/red]\n"
            f"    The clone's origin ({origin_url}) may be a fork or stale mirror.\n"
            f"    Re-run install.sh to reset the remote to {repo_url()}."
        )
    else:
        console.print(f"[red]✗  git fetch {remote} {branch} failed:[/red]\n{err}")
    raise SystemExit(1)


def _fetch_branch_tip(remote: str, target_branch: str, target_ref: str, origin_url: str) -> str:
    """Fetch <remote>/<branch> (and the tags) and return the SHA to land on."""
    # 1. Fetch the target with an EXPLICIT refspec so the remote-tracking ref
    #    (refs/remotes/origin/<branch>) is always written — a plain
    #    `git fetch origin main` leaves it stale on single-branch / custom-refspec
    #    clones, which is exactly how install.sh sets a host up. A genuinely
    #    missing branch (stale fork) fails here with "couldn't find remote ref".
    #
    #    --force applies to tags too: a rewritten upstream history (e.g. after
    #    stripping bad commit metadata) reassigns every tag to a new SHA, and a
    #    plain `--tags` fetch then rejects each one as "would clobber existing
    #    tag" — git still reports a non-zero exit even though the branch itself
    #    fetched fine, so self-update fails outright on any host with stale local
    #    tags. Self-update's whole point is hard-aligning to the remote, so tags
    #    should always defer to it, same as the branch reset below already does.
    refspec = f"+refs/heads/{target_branch}:refs/remotes/{remote}/{target_branch}"
    fetch = _git("fetch", "--force", "--tags", "--prune", remote, refspec, check=False)
    if fetch.returncode != 0:
        _fetch_failed(fetch.stderr.strip(), remote, target_branch, origin_url)

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
    return target_sha


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


def _restart_builder() -> None:
    """Restart the Rodeo Builder service when this host runs it, so it serves the new code."""
    from .. import systemd

    if not systemd.unit_is_active():
        return
    console.print(f"Restarting {systemd.BUILDER_UNIT}...")
    ok, why = systemd.restart_unit()
    if ok:
        console.print(f"[green]✓  {systemd.BUILDER_UNIT} restarted[/green] (it now serves the new page).")
    else:
        console.print(
            f"[yellow]⚠  {systemd.BUILDER_UNIT} did not restart: {why}\n"
            f"   It keeps serving the old page until you restart it yourself.[/yellow]"
        )


def run_self_update(branch: str | None = None, *, force: bool = False, restart: bool = True) -> None:
    """Update rodeo-cli to the latest validated release (or *branch*'s tip) and reinstall.

    Fetches the remote and hard-aligns the working tree to the target, then
    reinstalls. Fails loudly (raises
    SystemExit) rather than leaving the host on stale code. Shared by the
    `self-update` command and `clean --refresh`.
    """
    if not (_REPO_ROOT / ".git").exists():
        console.print(
            f"[red]✗  {_REPO_ROOT} is not a git repo — cannot self-update.[/red]\n"
            "Re-run the install script to get a fresh clone:\n"
            f"  curl -fsSL {default_install_url()} | bash"
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

    version_before = _installed_version()
    head_before = _git("rev-parse", "HEAD", check=False).stdout.strip()

    stable: str | None = None
    if branch is None:
        # The validated release: fetch main (where STABLE lives) and the tags.
        fetch = _git("fetch", "--force", "--tags", "--prune", remote,
                     f"+refs/heads/main:refs/remotes/{remote}/main", check=False)
        if fetch.returncode != 0:
            _fetch_failed(fetch.stderr.strip(), remote, "main", origin_url)
        stable = _stable_tag(remote)
        if stable is None:
            console.print(f"[yellow]⚠  {remote} has no release tags: following main instead.[/yellow]")

    if stable is not None:
        target_branch = None
        target_ref = stable
        console.print(
            f"Updating rodeo-cli from [dim]{_REPO_ROOT}[/dim]\n"
            f"  remote : [dim]{origin_url}[/dim]\n"
            f"  target : [dim]{target_ref}[/dim], the latest validated release "
            f"(--branch main for the latest code; currently {version_before})"
        )
        tip = _git("rev-parse", f"refs/tags/{stable}^{{commit}}", check=False)
        if tip.returncode != 0 or not tip.stdout.strip():
            console.print(f"[red]✗  could not resolve tag {stable} after fetch.[/red]")
            raise SystemExit(1)
        target_sha = tip.stdout.strip()
    else:
        target_branch = branch or _default_branch(remote)
        target_ref = f"{remote}/{target_branch}"
        console.print(
            f"Updating rodeo-cli from [dim]{_REPO_ROOT}[/dim]\n"
            f"  remote : [dim]{origin_url}[/dim]\n"
            f"  target : [dim]{target_ref}[/dim]  (currently {version_before})"
        )
        target_sha = _fetch_branch_tip(remote, target_branch, target_ref, origin_url)

    # 3. Hard-align the working tree to the target: a self-updating host must
    #    end on exactly that tag or origin/<branch>. Local work (uncommitted changes,
    #    or local commits the remote lacks) is never thrown away
    #    silently: rodeo often runs from a developer's own clone. --force discards it.
    #    Untracked files are ignored: checkout and reset --hard leave them alone.
    if head_before and head_before != target_sha:
        dirty = _git("status", "--porcelain", "--untracked-files=no", check=False).stdout.strip()
        if target_branch is not None:
            unpushed = _git("rev-list", f"{target_sha}..refs/heads/{target_branch}",
                            check=False).stdout.strip()
            where = f"on '{target_branch}'"
        else:
            # Commits on HEAD that no remote branch or tag has.
            unpushed = _git("rev-list", "HEAD", "--not", "--remotes", "--tags",
                            check=False).stdout.strip()
            where = "on HEAD"
        if (dirty or unpushed) and not force:
            what = " and ".join(w for w, on in (
                ("uncommitted changes", dirty),
                (f"local commits {where} that {remote} does not have", unpushed),
            ) if on)
            console.print(
                f"[red]✗  {_REPO_ROOT} has {what}; self-update would discard them.[/red]\n"
                "    Commit, push or stash them first, or re-run with --force to discard them."
            )
            raise SystemExit(1)
        if dirty or unpushed:
            console.print("[yellow]⚠  --force: discarding local changes to align with the remote.[/yellow]")
    if target_branch is not None:
        reset = _git("checkout", "-B", target_branch, target_sha, check=False)
    else:
        reset = _git("checkout", "--force", "--detach", target_sha, check=False)
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
    pip = [str(_VENV_PIP)] if _VENV_PIP.exists() else [sys.executable, "-m", "pip"]
    console.print("Reinstalling package...")
    r = subprocess.run([*pip, "install", "--quiet", "-e", str(_REPO_ROOT)], capture_output=False)
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
        return
    console.print(
        f"\n[bold green]✓  rodeo-cli updated to {version_after}[/bold green] "
        f"(was {version_before}, now at {target_ref} {target_sha[:8]})."
    )
    if restart:
        _restart_builder()


@click.command("self-update")
@click.option("--branch", default=None, metavar="NAME",
              help="Follow this branch's tip instead of the latest validated release "
                   "(--branch main for the latest code).")
@click.option("--force", is_flag=True, default=False,
              help="Discard uncommitted changes and unpushed local commits instead of refusing.")
@click.option("--restart/--no-restart", default=True, show_default=True,
              help="Restart the Rodeo Builder service (rodeo-builder.service) when this host runs it, "
                   "so it serves the updated page.")
def self_update_cmd(branch: str | None, force: bool, restart: bool) -> None:
    """Update rodeo-cli to the latest validated release and reinstall the CLI.

    The target is the release in STABLE on main: the last one that passed a
    live deploy. --branch follows a branch tip instead. Fetches, hard-aligns
    the working tree to the target, then reinstalls and, unless --no-restart,
    restarts the Rodeo Builder service on a host that runs it. Fails loudly
    rather than leaving the host on stale code.
    """
    run_self_update(branch, force=force, restart=restart)
