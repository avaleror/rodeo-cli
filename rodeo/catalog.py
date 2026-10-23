"""The rodeo catalog: published rodeos that live in their own git repositories.

Each entry is one file, ``rodeo/data/catalog.d/<name>.yaml``::

    name: ride
    title: Ride the range
    repo: https://github.com/<owner>/<repo>
    ref: main
    engine: rancher
    languages: [en, es]

A catalog rodeo's repository holds the files of a Rodeo Builder download
(``rodeo-plan.yaml``, ``story/``, ``checks/``, ``builder.yaml`` naming its base
profile...). :func:`install` fetches it (shallow, no credentials or prompts) and
installs it as the custom profile ``~/.rodeo/profiles/<name>/`` the way
``rodeo new --from-zip`` does, with ``rodeo.lock`` recording the repository,
ref and commit it came from.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import yaml

from .profile_install import ProfileFile, check_path, install_profile, manifest_base

CATALOG_DIR = Path(__file__).parent / "data" / "catalog.d"
LOCK = "rodeo.lock"
TIMEOUT = 300
_REPO_RE = re.compile(r"^https://[A-Za-z0-9.-]+(:\d+)?/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?(\.git)?$")
_REF_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]{0,199}$")

Run = Callable[..., "subprocess.CompletedProcess[str]"]


def entries(directory: Path = CATALOG_DIR) -> dict[str, dict[str, Any]]:
    """Every valid catalog entry by name; a file whose name, repo or ref is not plain is left out."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict) or data.get("name") != path.stem:
            continue
        repo, ref = str(data.get("repo") or ""), str(data.get("ref") or "")
        if _REPO_RE.match(repo) and (not ref or _REF_RE.match(ref)):
            out[path.stem] = {**data, "ref": ref}
    return out


def find(name: str, directory: Path = CATALOG_DIR) -> dict[str, Any] | None:
    return entries(directory).get(name)


def parse_spec(spec: str) -> tuple[str, str]:
    """(repo, ref) of ``https://<host>/<owner>/<repo>[@<ref>]``; ref "" is the default branch."""
    repo, _, ref = spec.strip().rpartition("@") if "@" in spec.split("://", 1)[-1] else (spec.strip(), "", "")
    if not _REPO_RE.match(repo):
        raise ValueError(f"{spec!r}: expected https://<host>/<owner>/<repo>[@<ref>]")
    if ref and not _REF_RE.match(ref):
        raise ValueError(f"{ref!r} is not a branch, tag or commit")
    return repo, ref


def _git(run: Run, args: list[str], step: str) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/true", "GIT_CONFIG_NOSYSTEM": "1"}
    try:
        r = run(["git", "-c", "core.hooksPath=/dev/null", *args], capture_output=True, text=True, env=env, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{step} took longer than {TIMEOUT} s") from exc
    if r.returncode != 0:
        raise RuntimeError(f"{step}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout.strip()


def read_dir(root: Path) -> list[ProfileFile]:
    """The builder files of a checkout (everything else, ``.git`` included, is left out)."""
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts):
        rel = path.relative_to(root).as_posix()
        try:
            check_path(rel)
        except ValueError:
            continue
        if path.is_symlink():
            raise ValueError(f"{rel} is a link")
        files.append(ProfileFile(rel, path.read_bytes(), os.access(path, os.X_OK) and rel.startswith("checks/")))
    return files


def install(name: str, repo: str, ref: str = "", force: bool = False, run: Run = subprocess.run) -> Path:
    """Fetch *repo* at *ref* and install it as the custom profile *name*; returns its directory."""
    with tempfile.TemporaryDirectory(prefix="rodeo-catalog-") as tmp:
        dest = Path(tmp) / "src"
        clone = ["clone", "-q", "--depth", "1", *(["--branch", ref] if ref else []), "--", repo, str(dest)]
        _git(run, clone, f"fetching {repo}")
        commit = _git(run, ["-C", str(dest), "rev-parse", "HEAD"], f"reading {repo}")
        files = read_dir(dest)
        profile = install_profile(name, files, base=manifest_base(files), force=force)
    (profile / LOCK).write_text(yaml.safe_dump({"repo": repo, "ref": ref, "commit": commit}, sort_keys=False))
    return profile
