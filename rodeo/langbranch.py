"""Language branches of a content repository: one branch per translation.

The default branch holds the source language; each translation lives in a
branch named after its language (``es``, ``pt-br``...). A language branch is
*behind* when the default branch has commits it hasn't taken in, leaving out
:data:`IGNORED` (the instructions themselves and CI)::

    git log $(git merge-base <source> <lang>)..<source> -- . ':!rmstory' ':!rmstory-stories' ':!story' ':!.github'

:func:`behind` runs that on a local clone (``rodeo story check``);
:func:`remote_behind` on a repository URL, from a history-only fetch (no file
contents), for ``rodeo up`` and ``rodeo content apply``.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

IGNORED = ("rmstory", "rmstory-stories", "story", ".github")
PATHSPEC = [".", *(f":!{path}" for path in IGNORED)]
TIMEOUT = 300
LANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})*$")
_AUTO_RE = re.compile(r"^[a-z]{2}(-[a-z0-9]{2,8})*$")
_MISSING = "couldn't find remote ref"

Run = Callable[..., "subprocess.CompletedProcess[str]"]


def _git(run: Run, repo: Path, args: list[str]) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/true"}
    try:
        r = run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
                capture_output=True, text=True, env=env, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git {args[0]} took longer than {TIMEOUT} s") from exc
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-300:] or f"git {args[0]} failed")
    return r.stdout.strip()


def behind(repo: Path, branch: str, source: str, run: Run = subprocess.run) -> list[str]:
    """Commits of *source* that *branch* hasn't taken in (``<hash> <subject>``, newest first),
    outside :data:`IGNORED`. Raises RuntimeError when git fails (e.g. an unknown branch)."""
    try:
        base = _git(run, repo, ["merge-base", source, branch])
    except RuntimeError as exc:
        if str(exc) == "git merge-base failed":
            raise RuntimeError(f"{branch} shares no history with {source}") from None
        raise
    out = _git(run, repo, ["log", "--oneline", f"{base}..{source}", "--", *PATHSPEC])
    return out.splitlines() if out else []


def default_branch(repo: Path, run: Run = subprocess.run) -> str:
    """The clone's default branch: ``origin/HEAD``'s target, else the checked-out branch."""
    try:
        return _git(run, repo, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    except RuntimeError:
        return _git(run, repo, ["symbolic-ref", "--short", "HEAD"])


def language_branches(repo: Path, source: str, run: Run = subprocess.run) -> dict[str, str]:
    """``{language: ref}`` for the clone's branches (local or ``origin/``) named like a
    two-letter language tag (``es``, ``pt-br``), the source branch left out."""
    refs = _git(run, repo, ["for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin"])
    source_name = source.removeprefix("origin/")
    out: dict[str, str] = {}
    for ref in refs.splitlines():
        name = ref.removeprefix("origin/")
        if name != source_name and _AUTO_RE.match(name):
            out.setdefault(name, ref)
    return dict(sorted(out.items()))


def remote_behind(url: str, branch: str, run: Run = subprocess.run) -> list[str] | None:
    """:func:`behind` for *branch* of the repository at *url* against its default branch, or
    None when it has no such branch. Raises RuntimeError when the repository can't be read."""
    with tempfile.TemporaryDirectory(prefix="rodeo-langbranch-") as tmp:
        repo = Path(tmp)
        _git(run, repo, ["init", "-q", "--bare"])
        fetch = ["fetch", "--ipv4", "-q", "--filter=blob:none", url]
        _git(run, repo, [*fetch, "+HEAD:refs/rodeo/source"])
        try:
            _git(run, repo, [*fetch, f"+refs/heads/{branch}:refs/rodeo/lang"])
        except RuntimeError as exc:
            if _MISSING in str(exc):
                return None
            raise
        try:
            return behind(repo, "refs/rodeo/lang", "refs/rodeo/source", run)
        except RuntimeError as exc:
            raise RuntimeError(str(exc).replace("refs/rodeo/lang", f"the {branch} branch")
                               .replace("refs/rodeo/source", "the default branch")) from None
