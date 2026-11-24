"""Managed lab SSH identity under ``~/.rodeo/ssh`` (hosts + nested VMs)."""
from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from typing import Any

from .config import ConfigError
from .providers.deadman import cloud_config_files, cloud_config_runcmd
from .fleet.inventory import FleetHost, FleetInventory
from .fleet.ssh_exec import run_remote
from .paths import fix_invoking_ownership, rodeo_ssh_dir

# EC2 key pair name for ImportKeyPair / RunInstances.
DEFAULT_EC2_KEY_NAME = "rodeo"
_PRIVATE_NAME = "id_ed25519"
_REMOTE_ROOT_KEY = "/root/.ssh/id_ed25519"
# Per-lab keys: ~/.rodeo/ssh/labs/<lab>/{id_ed25519,id_ed25519.pub,key_name,region}
_LABS_SUBDIR = "labs"
_KEY_NAME_FILE = "key_name"
_REGION_FILE = "region"
# The deployment id (issue #175): the 8 hex chars the key name ends with, kept
# in its own file so nothing has to parse the name.
_DEPLOYMENT_FILE = "deployment_id"
_DEPLOYMENT_RE = re.compile(r"[0-9a-f]{8}")


def build_ec2_userdata(
    *,
    ssh_user: str = "ec2-user",
    expires_at: datetime | None = None,
    boot_script: str | None = None,
    public_key: str | None = None,
) -> str:
    """cloud-config for passwordless root + NOPASSWD sudo for the AMI login user.

    Injects the managed pubkey into root (and ``ssh_user``) authorized_keys, enables
    ``PermitRootLogin prohibit-password``, and installs a sudoers drop-in so remote
    ``sudo -n`` / ``rodeo up`` never prompts for a password. With ``expires_at``
    it also arms the dead-man switch timer (rodeo/providers/deadman.py).
    ``boot_script`` (bash) is written to /usr/local/sbin/rodeo-boot.sh and
    started in the background (transient unit ``rodeo-boot``), so it never
    holds up cloud-init or SSH. Every login shell gets ZYPP_LOCK_TIMEOUT, so a
    zypper started over SSH waits for it instead of failing on the lock.
    ``public_key`` is the lab's own key (see new_lab_ssh_key); without it the
    machine-wide managed key is used.
    """
    import json

    if public_key is None:
        ensure_rodeo_ssh_key()
        public_key = rodeo_ssh_public_key_path().read_text()
    pub = public_key.strip()
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
        + (cloud_config_files(expires_at) if expires_at else "")
        + (_boot_script_files(boot_script) if boot_script else "")
        + "bootcmd:\n"
        "  - [mkdir, -p, /root/.ssh]\n"
        "  - [chmod, '700', /root/.ssh]\n"
        "runcmd:\n"
        "  - [chmod, '700', /root/.ssh]\n"
        "  - [bash, -lc, 'systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || true']\n"
        + (cloud_config_runcmd() if expires_at else "")
        + (
            "  - [systemd-run, --unit=rodeo-boot, --collect, /bin/bash, /usr/local/sbin/rodeo-boot.sh]\n"
            if boot_script else ""
        )
    )


def _boot_script_files(script: str) -> str:
    body = "".join(f"      {line}\n" for line in script.splitlines())
    return (
        "  - path: /usr/local/sbin/rodeo-boot.sh\n"
        "    owner: root:root\n"
        "    permissions: '0755'\n"
        "    content: |\n"
        + body
        + "  - path: /etc/profile.d/zz-rodeo-zypp.sh\n"
        "    owner: root:root\n"
        "    permissions: '0644'\n"
        "    content: |\n"
        "      export ZYPP_LOCK_TIMEOUT=600\n"
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
    _keygen(private, comment="rodeo-managed")
    return private


def _keygen(private: Path, *, comment: str) -> None:
    if shutil.which("ssh-keygen") is None:
        raise ConfigError("ssh-keygen not found on PATH — required to create ~/.rodeo/ssh key")
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(private), "-N", "", "-C", comment],
        check=True,
        capture_output=True,
        text=True,
    )
    _chmod_key_files(private, private.with_name(f"{private.name}.pub"))
    fix_invoking_ownership()


@dataclass(frozen=True)
class LabKey:
    """One lab's own SSH key pair and its EC2 key pair name."""

    workshop: str
    private: Path
    public: Path
    key_name: str
    region: str = ""
    deployment: str = ""


def _deployment_from_key_name(key_name: str) -> str:
    """Fallback for keys made before the deployment_id file existed."""
    last = key_name.rsplit("-", 1)[-1]
    return last if _DEPLOYMENT_RE.fullmatch(last) else ""


def _safe_lab(workshop: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", workshop).strip(".-") or "default"


def lab_ssh_root() -> Path:
    return rodeo_ssh_dir() / _LABS_SUBDIR


def lab_ssh_dir(workshop: str) -> Path:
    return lab_ssh_root() / _safe_lab(workshop)


def load_lab_ssh_key(workshop: str) -> LabKey | None:
    """The lab's key from ``~/.rodeo/ssh/labs/<lab>/``, or None if it has none."""
    d = lab_ssh_dir(workshop)
    private, public, name_file = d / _PRIVATE_NAME, d / f"{_PRIVATE_NAME}.pub", d / _KEY_NAME_FILE
    if not (private.is_file() and public.is_file() and name_file.is_file()):
        return None
    key_name = name_file.read_text().strip()
    if not key_name:
        return None
    region_file = d / _REGION_FILE
    region = region_file.read_text().strip() if region_file.is_file() else ""
    dep_file = d / _DEPLOYMENT_FILE
    deployment = dep_file.read_text().strip() if dep_file.is_file() else ""
    if not _DEPLOYMENT_RE.fullmatch(deployment):
        deployment = _deployment_from_key_name(key_name)
    return LabKey(workshop, private, public, key_name, region, deployment)


def new_lab_ssh_key(workshop: str, *, region: str = "") -> LabKey:
    """Generate a fresh key for one deployment of ``workshop``.

    Replaces whatever key the lab had: each deployment gets its own pair, and
    the EC2 key pair name carries a random suffix so two users (or two
    deployments) never share or overwrite one.
    """
    d = lab_ssh_dir(workshop)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, mode=0o700)
    private = d / _PRIVATE_NAME
    _keygen(private, comment=f"rodeo-lab-{_safe_lab(workshop)}")
    deployment = secrets.token_hex(4)
    key_name = f"rodeo-{_safe_lab(workshop)[:200]}-{deployment}"
    (d / _KEY_NAME_FILE).write_text(key_name + "\n")
    (d / _DEPLOYMENT_FILE).write_text(deployment + "\n")
    if region:
        (d / _REGION_FILE).write_text(region + "\n")
    fix_invoking_ownership()
    return LabKey(workshop, private, private.with_name(f"{_PRIVATE_NAME}.pub"), key_name, region,
                  deployment)


def delete_lab_ssh_key(workshop: str) -> None:
    """Remove the lab's local key (its EC2 key pair is deleted separately)."""
    d = lab_ssh_dir(workshop)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def list_lab_ssh_keys() -> list[LabKey]:
    root = lab_ssh_root()
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if d.is_dir():
            key = load_lab_ssh_key(d.name)
            if key is not None:
                out.append(key)
    return out


def _chmod_key_files(private: Path, public: Path) -> None:
    try:
        os.chmod(private, 0o600)
        if public.is_file():
            os.chmod(public, 0o644)
        os.chmod(private.parent, 0o700)
    except OSError:
        pass


def resolve_ssh_identity(explicit: str | None = None, *, workshop: str | None = None) -> str:
    """Return identity path: the lab's own key when ``workshop`` has one, else
    the machine-wide managed key (2A) — ``explicit`` is ignored."""
    if workshop:
        key = load_lab_ssh_key(workshop)
        if key is not None:
            return str(key.private)
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


def ensure_ec2_key_pair(
    ec2: Any,
    *,
    key_name: str = DEFAULT_EC2_KEY_NAME,
    public_key: Path | None = None,
    tags: dict[str, str] | None = None,
) -> str:
    """Ensure EC2 key pair ``key_name`` matches ``public_key`` (default: the
    managed pubkey); return name. ``tags`` go on the key pair when imported."""
    if public_key is None:
        ensure_rodeo_ssh_key()
    pub = public_key or rodeo_ssh_public_key_path()
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
                f"local key {pub} ({local_fp}). Delete the AWS key pair "
                f"or remove the local key and re-import."
            )
        return key_name

    kwargs: dict[str, Any] = {
        "KeyName": key_name,
        "PublicKeyMaterial": material.encode("utf-8"),
    }
    if tags:
        kwargs["TagSpecifications"] = [{
            "ResourceType": "key-pair",
            "Tags": [{"Key": k, "Value": v} for k, v in tags.items()],
        }]
    ec2.import_key_pair(**kwargs)
    return key_name


def delete_ec2_key_pair(ec2: Any, key_name: str) -> bool:
    """Best-effort DeleteKeyPair; True when AWS accepted it (or it was gone)."""
    if not key_name or key_name == DEFAULT_EC2_KEY_NAME:
        return False  # never the shared machine-wide pair
    try:
        ec2.delete_key_pair(KeyName=key_name)
        return True
    except Exception as exc:  # noqa: BLE001 — botocore ClientError
        return "NotFound" in str(exc)


def _fingerprints_match(local_fp: str, remote_fp: str) -> bool:
    """Compare a local ``SHA256:…`` fingerprint with EC2's KeyFingerprint.

    The two sides format the same digest differently, so a plain string compare
    reports a mismatch for a key pair rodeo imported itself:

      ssh-keygen -lf : SHA256:kCvXKSed…X6pY     (prefixed, padding stripped)
      EC2            :        kCvXKSed…X6pY=    (bare, base64 padding kept)

    Normalise both to unpadded base64 before comparing. Only the padding and
    prefix are cosmetic — the digest itself must match exactly.
    """
    a = local_fp.strip().removeprefix("SHA256:").rstrip("=")
    b = remote_fp.strip().removeprefix("SHA256:").rstrip("=")
    return bool(a) and bool(b) and a == b


def plant_rodeo_ssh_key(
    inventory: FleetInventory,
    host: FleetHost,
    *,
    timeout: float = 120.0,
    key: LabKey | None = None,
) -> None:
    """Copy the lab key (default: managed key) to ``/root/.ssh/id_ed25519`` on
    the KVM host (idempotent)."""
    if key is not None:
        private, public = key.private, key.public
    else:
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

    # The private key goes over stdin, never argv: argv is readable by any
    # local user in /proc/*/cmdline, on the laptop and on the host.
    pub_b64 = base64.b64encode(pub_text.encode()).decode()
    script = (
        "set -euo pipefail; "
        "sudo -n mkdir -p /root/.ssh; "
        "sudo -n chmod 700 /root/.ssh; "
        f"sudo -n install -m 600 /dev/null {_REMOTE_ROOT_KEY}; "
        f"sudo -n tee {_REMOTE_ROOT_KEY} >/dev/null; "
        f"echo {pub_b64} | base64 -d | sudo -n tee {_REMOTE_ROOT_KEY}.pub >/dev/null; "
        f"sudo -n chmod 644 {_REMOTE_ROOT_KEY}.pub"
    )
    result = run_remote(
        inventory,
        host,
        ["bash", "-c", script],
        timeout=timeout,
        stdin=priv_text,
    )
    if not result.ok:
        msg = (result.stderr or result.stdout or f"exit {result.rc}").strip()
        raise ConfigError(
            f"failed to plant SSH key on {host.id} (need passwordless sudo): {msg[:300]}"
        )
