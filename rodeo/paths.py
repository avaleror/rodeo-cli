"""Canonical paths under ~/.rodeo.

Always resolve to the invoking user's home directory, even when rodeo re-execs
under ``sudo`` (``SUDO_USER`` is set, ``HOME`` is ``/root``).
"""
from __future__ import annotations

import os
from pathlib import Path


def invoking_home() -> Path:
    """Real user's home directory even when running under plain ``sudo``."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            import pwd

            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except (KeyError, ImportError):
            pass
    return Path.home()


def rodeo_dir() -> Path:
    return invoking_home() / ".rodeo"


def rodeo_secrets_path() -> Path:
    return rodeo_dir() / "secrets.yaml"


def rodeo_state_dir() -> Path:
    return rodeo_dir() / "state"


def rodeo_logs_dir() -> Path:
    return rodeo_dir() / "logs"


def rodeo_profiles_dir() -> Path:
    return rodeo_dir() / "profiles"


def rodeo_last_lab_file() -> Path:
    return rodeo_dir() / "last_lab"


def harvester_kubeconfig_path() -> Path:
    return rodeo_dir() / "harvester-kubeconfig"


def fix_invoking_ownership() -> None:
    """Hand ``~/.rodeo`` back to the invoking user after a self-escalated run.

    Every path in this module resolves to the invoking user's home, but files
    created by the escalated *root* process (state, logs, kubeconfig, last_lab)
    are still owned by root — read-only commands (``status``, ``plan``) run as
    the plain user afterward would otherwise need ``sudo`` just to open them.
    No-op unless running as root under ``SUDO_USER`` (nothing to hand back
    when genuinely root with no invoking user, e.g. a root-only host).
    """
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user or os.geteuid() != 0:
        return
    try:
        import pwd

        pw = pwd.getpwnam(sudo_user)
    except (KeyError, ImportError):
        return
    root_path = rodeo_dir()
    if not root_path.exists():
        return
    for path in (root_path, *root_path.rglob("*")):
        try:
            os.chown(path, pw.pw_uid, pw.pw_gid)
        except OSError:
            pass