"""The MCP lab allowlist: ``~/.rodeo/labs.yaml``, edited by ``rodeo lab add/remove``.

While the file does not exist, MCP tools accept any directory (today's
behaviour) and the server prints one warning. Once it exists, even with an
empty list, every path an MCP tool receives must resolve (symlinks and ``..``
followed, relative paths taken from the tool's working directory) to a listed
lab or a path inside one.

Like the trust file, this is ordinary user data: a mistake filter for MCP
clients and confused models, not a boundary against a process running as the
same user, which can edit the file.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path

import yaml

from .config import ConfigError
from .paths import fix_invoking_ownership, rodeo_labs_allowlist_path

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def allowlist_path() -> Path:
    return rodeo_labs_allowlist_path()


def _parse(raw: bytes, path: Path) -> list[str]:
    try:
        data = yaml.safe_load(raw.decode()) if raw.strip() else {"labs": []}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path}: corrupt lab list: {exc}")
    labs = data.get("labs") if isinstance(data, dict) else None
    if labs is None and isinstance(data, dict) and not data:
        labs = []
    if not isinstance(labs, list) or not all(isinstance(p, str) for p in labs):
        raise ConfigError(f"{path}: corrupt lab list (expected 'labs:' with a list of directories)")
    return labs


def load_labs() -> list[str] | None:
    """Listed lab realpaths, or None when the file does not exist."""
    path = allowlist_path()
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read the lab list: {exc}")
    with os.fdopen(fd, "rb") as fh:
        fcntl.flock(fh, fcntl.LOCK_SH)
        return _parse(fh.read(), path)


def _update(fn) -> list[str]:
    path = allowlist_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | _NOFOLLOW, 0o600)
    except OSError as exc:
        raise ConfigError(f"{path}: cannot open the lab list: {exc}")
    with os.fdopen(fd, "r+b") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        os.fchmod(fh.fileno(), 0o600)
        labs = fn(_parse(fh.read(), path))
        fh.seek(0)
        fh.truncate()
        fh.write(yaml.safe_dump({"labs": labs}, default_flow_style=False).encode())
        fh.flush()
        os.fsync(fh.fileno())
    fix_invoking_ownership()
    return labs


def add_lab(path: str | os.PathLike) -> str:
    """Append ``path``'s realpath. It must be an existing directory."""
    real = os.path.realpath(os.path.expanduser(str(path)))
    if not os.path.isdir(real):
        raise ConfigError(f"{path}: not a directory")
    _update(lambda labs: labs if real in labs else [*labs, real])
    return real


def remove_lab(path: str | os.PathLike) -> bool:
    """Drop ``path`` (as given or as its realpath). True when it was listed."""
    given = os.path.expanduser(str(path))
    keys = {given, os.path.abspath(given), os.path.realpath(given)}
    labs = load_labs() or []
    if not keys & set(labs):
        return False
    _update(lambda current: [p for p in current if p not in keys])
    return True


def resolve_arg(value: str, base: str | os.PathLike | None) -> str:
    """Realpath of a tool path argument, relative to ``base`` (the tool's cwd)."""
    expanded = os.path.expanduser(str(value))
    if not os.path.isabs(expanded):
        expanded = os.path.join(str(base) if base else os.getcwd(), expanded)
    return os.path.realpath(expanded)


def is_listed(real: str, labs: list[str]) -> bool:
    """True when ``real`` (already a realpath) is a listed lab or inside one."""
    for lab in labs:
        lab_real = os.path.realpath(lab)
        try:
            if os.path.commonpath([real, lab_real]) == lab_real:
                return True
        except ValueError:
            continue
    return False
