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


def update_admin_passwords(path: Path, new_password: str, keys: set[str]) -> None:
    """Rewrite only the given service-password keys in an existing secrets.yaml.

    Used by 'rodeo set-password' to rotate the Harvester/Rancher dashboard
    passwords without touching harvester_os_password, rancher_vm_password,
    harvester_token, gitea_admin_password, or any comments/custom lines —
    write_secrets_file() rewrites the whole file to one shared password and
    would silently drop fields like gitea_admin_password that it doesn't know
    about.
    """
    lines = path.read_text().splitlines(keepends=True)
    out = []
    for line in lines:
        key = line.split(":", 1)[0].strip()
        if key in keys:
            out.append(f'{key}: "{new_password}"\n')
        else:
            out.append(line if line.endswith("\n") else line + "\n")
    path.write_text("".join(out))
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


def plan_secret_keys(obj: object) -> set[str]:
    """Every plain ``??key`` placeholder in a plan (not the ??env:/??file:/??cmd: forms)."""
    keys: set[str] = set()
    if isinstance(obj, str):
        spec = obj[2:] if obj.startswith("??") else ""
        if spec and ":" not in spec:
            keys.add(spec)
    elif isinstance(obj, dict):
        for value in obj.values():
            keys |= plan_secret_keys(value)
    elif isinstance(obj, list):
        for value in obj:
            keys |= plan_secret_keys(value)
    return keys


def _read_secrets(path: Path) -> dict:
    import yaml

    try:
        return yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}


def append_secret(path: Path, key: str, value: str) -> None:
    """Add one key to secrets.yaml (0600), keeping every existing line and comment."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else ""
    sep = "" if not existing or existing.endswith("\n") else "\n"
    # JSON string quoting is valid YAML and safe for any character.
    path.write_text(f"{existing}{sep}{key}: {json.dumps(value)}\n")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600


def ensure_plan_secrets(
    plan: dict, path: Path | None = None, lab_path: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Generate the plan's missing ``??key`` secrets. Return (generated, missing_operator).

    Keys listed in the plan's ``operator_secrets:`` are never generated — they
    are values only the operator has (an SCC regcode, the admin password a
    pre-built image was made with) and are returned as missing instead.
    Generated ones go to ``lab_path`` (the lab's own .rodeo-secrets.yaml, so
    every lab instance gets its own) when given, else to the global file.
    """
    from .labinabox import apply_variant

    from .labinabox_host import cloud_secret_keys

    plan = apply_variant(plan)  # secrets of unselected lab-in-a-box variants aren't needed
    path = path or secrets_path()
    # Cloud credentials are always the operator's: never generate a random one.
    operator = {str(k) for k in plan.get("operator_secrets") or []} | cloud_secret_keys(plan)
    have = {**_read_secrets(path), **(_read_secrets(lab_path) if lab_path else {})}
    generated, missing = [], []
    for key in sorted(plan_secret_keys(plan)):
        if have.get(key) not in (None, ""):
            continue
        if key in operator:
            missing.append(key)
            continue
        append_secret(lab_path or path, key, random_password())
        generated.append(key)
    return generated, missing
