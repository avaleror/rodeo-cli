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