"""Managed lab SSH identity under ``~/.rodeo/ssh`` (hosts + nested VMs)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import ConfigError
from .fleet.inventory import FleetHost, FleetInventory
from .fleet.ssh_exec import run_remote
from .paths import fix_invoking_ownership, rodeo_ssh_dir

# EC2 key pair name for ImportKeyPair / RunInstances.
DEFAULT_EC2_KEY_NAME = "rodeo"
_PRIVATE_NAME = "id_ed25519"
_REMOTE_ROOT_KEY = "/root/.ssh/id_ed25519"


def build_ec2_userdata(*, ssh_user: str = "ec2-user") -> str:
    """cloud-config for passwordless root + NOPASSWD sudo for the AMI login user.

    Injects the managed pubkey into root (and ``ssh_user``) authorized_keys, enables
    ``PermitRootLogin prohibit-password``, and installs a sudoers drop-in so remote
    ``sudo -n`` / ``rodeo up`` never prompts for a password.
    """
    import json

    ensure_rodeo_ssh_key()
    pub = rodeo_ssh_public_key_path().read_text().strip()
    if not pub:
        raise ConfigError("managed SSH public key is empty")
    user = (ssh_user or "ec2-user").strip() or "ec2-user"
    pub_json = json.dumps(pub)
    return (
        "#cloud-config\n"
        "ssh_pwauth: false\n"
        "disable_root: false\n"
        "users:\n"
        "  - name: root\n"
        "    lock_passwd: true\n"
        "    ssh_authorized_keys:\n"
        f"      - {pub_json}\n"
        f"  - name: {user}\n"
        "    lock_passwd: true\n"
        "    sudo: ALL=(ALL) NOPASSWD:ALL\n"
        "    groups: [wheel]\n"
        "    ssh_authorized_keys:\n"
        f"      - {pub_json}\n"
        "write_files:\n"
        "  - path: /etc/sudoers.d/90-rodeo\n"
        "    owner: root:root\n"
        "    permissions: '0440'\n"
        "    content: |\n"
        f"      {user} ALL=(ALL) NOPASSWD:ALL\n"
        f"      Defaults:{user} !requiretty\n"
        "  - path: /root/.ssh/authorized_keys\n"
        "    owner: root:root\n"
        "    permissions: '0600'\n"
        "    content: |\n"
        f"      {pub}\n"
        "  - path: /etc/ssh/sshd_config.d/99-rodeo-root.conf\n"
        "    owner: root:root\n"
        "    permissions: '0644'\n"
        "    content: |\n"
        "      PermitRootLogin prohibit-password\n"
        "bootcmd:\n"
        "  - [mkdir, -p, /root/.ssh]\n"
        "  - [chmod, '700', /root/.ssh]\n"
        "runcmd:\n"
        "  - [chmod, '700', /root/.ssh]\n"
        "  - [bash, -lc, 'systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || true']\n"
    )


def rodeo_ssh_private_key_path() -> Path:
    return rodeo_ssh_dir() / _PRIVATE_NAME


def rodeo_ssh_public_key_path() -> Path:
    return rodeo_ssh_dir() / f"{_PRIVATE_NAME}.pub"


def ensure_rodeo_ssh_key() -> Path:
    """Create ``~/.rodeo/ssh/id_ed25519`` if missing; return private key path."""
    ssh_dir = rodeo_ssh_dir()
    ssh_dir.mkdir(parents=True, exist_ok=True)
    private = rodeo_ssh_private_key_path()
    public = rodeo_ssh_public_key_path()
    if private.is_file() and public.is_file():
        _chmod_key_files(private, public)
        return private
    if private.is_file() and not public.is_file():
        raise ConfigError(
            f"managed SSH private key exists without pubkey: {private} "
            f"(restore {public} or remove the private key and re-run)"
        )
    if shutil.which("ssh-keygen") is None:
        raise ConfigError("ssh-keygen not found on PATH — required to create ~/.rodeo/ssh key")
    subprocess.run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-f",
            str(private),
            "-N",
            "",
            "-C",
            "rodeo-managed",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    _chmod_key_files(private, public)
    fix_invoking_ownership()
    return private


def _chmod_key_files(private: Path, public: Path) -> None:
    try:
        os.chmod(private, 0o600)
        if public.is_file():
            os.chmod(public, 0o644)
        os.chmod(private.parent, 0o700)
    except OSError:
        pass


def resolve_ssh_identity(explicit: str | None = None) -> str:
    """Return identity path: managed key (2A) — ``explicit`` ignored when empty."""
    if explicit and str(explicit).strip():
        # 2A: rodeo owns the pair; ignore BYO paths and always use managed key.
        pass
    return str(ensure_rodeo_ssh_key())


def local_pubkey_sha256(pub_path: Path | None = None) -> str:
    """Return ``SHA256:…`` fingerprint for the managed (or given) public key."""
    path = pub_path or rodeo_ssh_public_key_path()
    if not path.is_file():
        ensure_rodeo_ssh_key()
        path = rodeo_ssh_public_key_path()
    try:
        out = subprocess.check_output(
            ["ssh-keygen", "-lf", str(path), "-E", "sha256"],
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ConfigError(f"cannot fingerprint {path}: {exc}") from exc
    for part in out.split():
        if part.startswith("SHA256:"):
            return part
    raise ConfigError(f"unexpected ssh-keygen fingerprint output for {path}: {out!r}")


def ensure_ec2_key_pair(ec2: Any, *, key_name: str = DEFAULT_EC2_KEY_NAME) -> str:
    """Ensure EC2 key pair ``key_name`` matches local managed pubkey; return name."""
    ensure_rodeo_ssh_key()
    pub = rodeo_ssh_public_key_path()
    material = pub.read_text().strip()
    local_fp = local_pubkey_sha256(pub)
    try:
        resp = ec2.describe_key_pairs(KeyNames=[key_name])
        pairs = resp.get("KeyPairs") or []
    except Exception as exc:  # noqa: BLE001 — botocore ClientError
        err_name = type(exc).__name__
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if "InvalidKeyPair.NotFound" in str(exc) or code == "InvalidKeyPair.NotFound":
            pairs = []
        elif "NotFound" in err_name or "NotFound" in str(exc):
            pairs = []
        else:
            # Fake clients may lack describe_key_pairs — treat as missing.
            if not hasattr(ec2, "describe_key_pairs"):
                pairs = []
            else:
                raise ConfigError(f"describe_key_pairs failed: {exc}") from exc

    if pairs:
        remote_fp = str(pairs[0].get("KeyFingerprint") or "").strip()
        if not _fingerprints_match(local_fp, remote_fp):
            raise ConfigError(
                f"EC2 key pair {key_name!r} fingerprint {remote_fp!r} does not match "
                f"local ~/.rodeo/ssh key ({local_fp}). Delete the AWS key pair "
                f"or remove ~/.rodeo/ssh and re-import."
            )
        return key_name

    ec2.import_key_pair(KeyName=key_name, PublicKeyMaterial=material.encode("utf-8"))
    return key_name


def _fingerprints_match(local_fp: str, remote_fp: str) -> bool:
    a = local_fp.strip().removeprefix("SHA256:")
    b = remote_fp.strip().removeprefix("SHA256:")
    return bool(a) and bool(b) and a == b


def plant_rodeo_ssh_key(
    inventory: FleetInventory,
    host: FleetHost,
    *,
    timeout: float = 120.0,
) -> None:
    """Copy managed key to ``/root/.ssh/id_ed25519`` on the KVM host (idempotent)."""
    private = ensure_rodeo_ssh_key()
    public = rodeo_ssh_public_key_path()
    priv_text = private.read_text()
    pub_text = public.read_text().strip() + "\n"

    # Compare remote pubkey; skip if identical.
    check = run_remote(
        inventory,
        host,
        [
            "bash",
            "-lc",
            f"sudo -n cat {_REMOTE_ROOT_KEY}.pub 2>/dev/null || true",
        ],
        timeout=timeout,
    )
    if check.ok and check.stdout.strip() == pub_text.strip():
        return

    # Write via sudo tee so ec2-user / sles can plant into /root.
    import base64

    priv_b64 = base64.b64encode(priv_text.encode()).decode()
    pub_b64 = base64.b64encode(pub_text.encode()).decode()
    script = (
        "set -euo pipefail; "
        "sudo mkdir -p /root/.ssh; "
        f"echo {priv_b64} | base64 -d | sudo -n tee {_REMOTE_ROOT_KEY} >/dev/null; "
        f"echo {pub_b64} | base64 -d | sudo -n tee {_REMOTE_ROOT_KEY}.pub >/dev/null; "
        f"sudo -n chmod 600 {_REMOTE_ROOT_KEY}; "
        f"sudo -n chmod 644 {_REMOTE_ROOT_KEY}.pub; "
        "sudo -n chmod 700 /root/.ssh"
    )
    result = run_remote(
        inventory,
        host,
        ["bash", "-lc", script],
        timeout=timeout,
    )
    if not result.ok:
        msg = (result.stderr or result.stdout or f"exit {result.rc}").strip()
        raise ConfigError(
            f"failed to plant SSH key on {host.id} (need passwordless sudo): {msg[:300]}"
        )
