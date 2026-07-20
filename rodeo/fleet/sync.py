"""Sync lab content onto a remote host (git or profile seed)."""
from __future__ import annotations

import shlex

from ..config import ConfigError
from .inventory import FleetInventory


def normalize_git_url(source: str) -> str:
    """Accept ``git:https://…``, bare https, or SSH git URLs."""
    s = source.strip()
    if s.startswith("git:"):
        s = s[4:].strip()
    if not s:
        raise ConfigError("lab.source is empty")
    return s


def sync_script(inventory: FleetInventory) -> str:
    """Shell fragment to ensure ``lab.dir`` exists with the desired content."""
    lab = shlex.quote(inventory.lab_dir)
    if inventory.lab_source:
        url = shlex.quote(normalize_git_url(inventory.lab_source))
        if inventory.lab_branch:
            br = shlex.quote(inventory.lab_branch)
            update = (
                f"git -C {lab} fetch --depth 1 origin {br} && "
                f"git -C {lab} checkout {br} && "
                f"git -C {lab} pull --ff-only"
            )
            clone = (
                f"mkdir -p $(dirname {lab}) && "
                f"git clone --depth 1 --branch {br} {url} {lab}"
            )
        else:
            update = f"git -C {lab} pull --ff-only"
            clone = f"mkdir -p $(dirname {lab}) && git clone --depth 1 {url} {lab}"
        return (
            f"if [ -d {lab}/.git ]; then {update}; "
            f"elif [ -e {lab} ]; then "
            f'echo "lab.dir exists but is not a git repo: {inventory.lab_dir}" >&2; exit 1; '
            f"else {clone}; fi"
        )

    if inventory.lab_profile:
        profile = shlex.quote(inventory.lab_profile)
        target = shlex.quote(inventory.lab_target)
        return (
            f"mkdir -p {lab} && "
            f"rodeo up --yes --no-tmux --no-deploy --profile {profile} "
            f"--dir {lab} --target {target}"
        )

    raise ConfigError("lab.source or lab.profile required to sync")
