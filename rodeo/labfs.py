"""Writes rodeo makes inside a lab directory, without following planted symlinks.

The paths rodeo writes (content.lock, guide/, .labinabox/, workshop-guide/...)
are left out of the lab hash, so a symlink planted there after `rodeo trust`
would otherwise redirect a root write anywhere. Every directory on the way is
checked, and files are written to a temp file and renamed over the target,
which replaces a symlink instead of writing through it.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .config import ConfigError


def lab_subdir(lab: Path, rel: str | Path, mode: int = 0o700) -> Path:
    """``lab/rel`` as a real directory (created if missing); refuses a symlink
    anywhere between the lab and it."""
    cur = Path(lab)
    for part in Path(rel).parts:
        if part in ("", ".", ".."):
            raise ConfigError(f"{rel}: not a plain path inside the lab")
        cur = cur / part
        if cur.is_symlink():
            raise ConfigError(f"{cur} is a symlink: rodeo does not write through links in a lab")
        if not cur.exists():
            cur.mkdir(mode=mode)
        elif not cur.is_dir():
            raise ConfigError(f"{cur} is not a directory")
    return cur


def write_lab_file(lab: Path, rel: str | Path, data: str | bytes, mode: int = 0o600) -> Path:
    """Atomically write ``lab/rel`` (temp file + rename in a checked directory)."""
    rel = Path(rel)
    parent = lab_subdir(lab, rel.parent) if rel.parent != Path(".") else Path(lab)
    if Path(lab).is_symlink():
        raise ConfigError(f"{lab} is a symlink: rodeo does not write through links")
    fd, tmp = tempfile.mkstemp(prefix=f".{rel.name}.", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(data.encode() if isinstance(data, str) else data)
        os.replace(tmp, parent / rel.name)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return parent / rel.name


def clear_lab_path(lab: Path, rel: str | Path) -> Path:
    """Remove ``lab/rel`` before rodeo rebuilds it: a symlink is unlinked, never followed."""
    import shutil

    rel = Path(rel)
    parent = lab_subdir(lab, rel.parent) if rel.parent != Path(".") else Path(lab)
    path = parent / rel.name
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    return path
