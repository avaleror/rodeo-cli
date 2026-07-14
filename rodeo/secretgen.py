"""Shared secret generation for init / generate / up.

One place that knows how to make a Rancher-valid password, a cluster join
token, and how to write (or reuse) ~/.rodeo/secrets.yaml. Commands import these
helpers instead of each rolling their own, so the rules stay in sync.
"""
from __future__ import annotations

import secrets
import stat
import string
from pathlib import Path

from .paths import rodeo_secrets_path


def secrets_path() -> Path:
    """Default secrets location (invoking user's ~/.rodeo, sudo-safe)."""
    return rodeo_secrets_path()


def random_password(length: int = 16) -> str:
    """Random password that satisfies Rancher complexity (upper+lower+digit, 12+ chars)."""
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.isdigit() for c in pw) and any(c.isupper() for c in pw)
                and any(c.islower() for c in pw)):
            return pw


def gen_token() -> str:
    """Random Harvester cluster join token."""
    return secrets.token_urlsafe(24)


def write_secrets_file(path: Path, password: str, token: str) -> None:
    """Write ~/.rodeo/secrets.yaml (chmod 600) with explicit per-service passwords + token.

    One shared ``password`` covers every VM-console/admin credential across all
    profiles (harvester-* and suse-edge alike) — same convention as
    ``harvester_os_password``/``harvester_admin_password``/``rancher_admin_password``.
    ``rancher_vm_password`` is suse-edge's OS console password for the Rancher/EIB
    VMs; without it here, ``??rancher_vm_password`` in that profile's plan never
    resolves and the deploy fails closed on a missing secret.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# ~/.rodeo/secrets.yaml — kept out of version control\n"
        "#\n"
        "# harvester_os_password    — OS console (rancher user SSH/TTY)\n"
        "# harvester_admin_password — Harvester web UI admin account\n"
        "# rancher_admin_password   — Rancher web UI admin account\n"
        "# rancher_vm_password      — OS console for suse-edge's Rancher/EIB VMs\n"
        "# harvester_token          — cluster join token (shared by all nodes)\n"
        f'harvester_os_password: "{password}"\n'
        f'harvester_admin_password: "{password}"\n'
        f'rancher_admin_password: "{password}"\n'
        f'rancher_vm_password: "{password}"\n'
        f'harvester_token: "{token}"\n'
    )
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600


def read_secrets_file(path: Path | None = None) -> tuple[str | None, str | None]:
    """Return (password, token) parsed from an existing secrets file, or (None, None)."""
    path = path or secrets_path()
    password = token = None
    try:
        for line in path.read_text().splitlines():
            if line.startswith("harvester_os_password:"):
                password = line.split(":", 1)[1].strip().strip("\"'")
            elif line.startswith("harvester_token:"):
                token = line.split(":", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return password, token


def ensure_secrets_file(path: Path | None = None, force: bool = False) -> tuple[str, str, bool]:
    """Make sure a usable secrets file exists. Return (password, token, created).

    Reuses existing values unless ``force``. Generates and writes fresh ones when
    missing (or unparseable). This is the silent, file-based path that lets deploy
    read ``??key`` placeholders with no env vars and no ``sudo -E``.
    """
    path = path or secrets_path()
    if path.exists() and not force:
        password, token = read_secrets_file(path)
        if password and token:
            return password, token, False
    password = random_password()
    token = gen_token()
    write_secrets_file(path, password, token)
    return password, token, True
