"""Root escalation without the ``sudo -E`` dance.

`rodeo up` needs root for the privileged deploy phases (kvm_host, libvirt). Rather
than make a beginner remember ``sudo -E`` and ``export RODEO=…``, the command
re-executes itself under sudo. Because secrets live in ~/.rodeo/secrets.yaml (file
form, resolved by config.py), no environment needs to be forwarded.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def is_root() -> bool:
    return os.geteuid() == 0


def find_rodeo_bin() -> str:
    """Locate the rodeo entry point to re-exec (same pattern as bootstrap)."""
    return shutil.which("rodeo") or os.path.abspath(sys.argv[0])


def relaunch_as_root(argv: list[str]) -> "None":
    """Replace this process with ``sudo <rodeo> <argv...>``. Does not return on success.

    No ``-E``: the child reads ~/.rodeo/secrets.yaml directly, so the parent
    environment is irrelevant.
    """
    if shutil.which("sudo") is None:
        raise RuntimeError(
            "This step needs root and 'sudo' was not found. "
            "Re-run as root, e.g. 'su -' then the same command."
        )
    rodeo = find_rodeo_bin()
    os.execvp("sudo", ["sudo", rodeo, *argv])  # noqa: S606 — intentional re-exec


def ensure_root(argv: list[str]) -> None:
    """If not already root, re-exec under sudo with ``argv``. Returns only when root."""
    if is_root():
        return
    relaunch_as_root(argv)


def sudo_prefix() -> list[str]:
    """['sudo'] when escalation is needed and possible, else []. For one-off commands."""
    if is_root() or shutil.which("sudo") is None:
        return []
    return ["sudo"]


# Re-exported for callers that build their own relaunch argv.
def home_of_invoking_user() -> Path:
    """Best-effort real user's home even under sudo (for ~/.rodeo locations)."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            import pwd
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except (KeyError, ImportError):
            pass
    return Path.home()
