"""Laptop → KVM-host SSH via OpenSSH subprocess (fleet control plane).

Separate from ``rodeo.ssh`` which is for host→VM lab connections.
"""
from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .inventory import FleetHost, FleetInventory


@dataclass(frozen=True)
class RemoteResult:
    """Outcome of one remote command."""

    host_id: str
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


def _target(host: FleetHost, inventory: FleetInventory) -> str:
    """Return user@host for ssh argv."""
    if "@" in host.ssh:
        return host.ssh
    user = host.ssh_user or inventory.ssh_user
    return f"{user}@{host.ssh}"


def _identity(inventory: FleetInventory) -> str | None:
    """``defaults.identity_file`` if set, else rodeo's managed key when it exists.

    Provisioned hosts only trust the managed key (cloud-init userdata), so without
    this fallback every fleet command against them fails with "Permission denied"
    unless the operator also sets ``identity_file`` by hand. Agent keys still work
    for BYO hosts: ``-i`` adds an identity, it does not exclude the others.
    """
    if inventory.identity_file:
        return inventory.identity_file
    from ..ssh_key import rodeo_ssh_private_key_path  # lazy: ssh_key imports this module

    managed = rodeo_ssh_private_key_path()
    return str(managed) if managed.is_file() else None


def known_hosts_path(workshop: str) -> Path:
    """Per-workshop known_hosts under ``~/.rodeo/fleet/<workshop>/``."""
    from ..paths import rodeo_dir

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", workshop).strip(".-") or "default"
    return rodeo_dir() / "fleet" / safe / "known_hosts"


def forget_host_key(workshop: str, address: str) -> None:
    """Drop ``address`` from the workshop's known_hosts (a new instance may reuse an
    IP). Best effort: a missing file or ssh-keygen is not an error."""
    path = known_hosts_path(workshop)
    if not path.is_file() or not address:
        return
    try:
        subprocess.run(["ssh-keygen", "-R", address, "-f", str(path)],
                       capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass
    Path(f"{path}.old").unlink(missing_ok=True)


def ssh_argv(
    inventory: FleetInventory,
    host: FleetHost,
    remote_command: str,
) -> list[str]:
    """Build OpenSSH argv to run ``remote_command`` on ``host``.

    Host keys are trusted on first use and then pinned in a per-workshop known_hosts
    file (``accept-new``): fresh hosts connect without a prompt, but a changed key on
    a known address fails instead of silently sending lab secrets to an impostor.
    Provisioning forgets the key of every address it (re)creates.
    """
    argv = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=15",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={known_hosts_path(inventory.name)}",
    ]
    identity = _identity(inventory)
    if identity:
        argv.extend(["-i", identity])
    for opt in inventory.ssh_options:
        # Allow either full "-o Foo=bar" pieces or bare "Foo=bar"
        if opt.startswith("-"):
            argv.append(opt)
        else:
            argv.extend(["-o", opt])
    argv.append(_target(host, inventory))
    argv.append(remote_command)
    return argv


def _remote_user(host: FleetHost, inventory: FleetInventory) -> str:
    return _target(host, inventory).rsplit("@", 1)[0]


def run_remote(
    inventory: FleetInventory,
    host: FleetHost,
    argv: Sequence[str],
    *,
    timeout: float = 120.0,
    as_root: bool = True,
    stdin: str | None = None,
) -> RemoteResult:
    """Run ``argv`` on the remote host (joined with shlex) via OpenSSH.

    ``argv`` is the remote command tokens (e.g. ``[\"rodeo\", \"doctor\", \"--output\", \"json\"]``).

    Fleet state lives in root's ``~/.rodeo`` and ``lab.dir`` defaults under ``/root``,
    so with a non-root ``ssh_user`` (``ec2-user``, ``sles``) the command runs through
    ``sudo -n -H bash -lc`` (the same pattern as single-host ``--target aws``).
    ``as_root=False`` runs it as the login user, for probes that check sudo itself.
    ``stdin`` feeds the remote command: use it for anything secret, never argv.
    """
    known_hosts_path(inventory.name).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    remote_command = " ".join(shlex.quote(a) for a in argv)
    if as_root and _remote_user(host, inventory) != "root":
        remote_command = f"sudo -n -H bash -lc {shlex.quote(remote_command)}"
    cmd = ssh_argv(inventory, host, remote_command)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin,
        )
        return RemoteResult(
            host_id=host.id,
            rc=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        err = (exc.stderr or "") if isinstance(exc.stderr, str) else f"timeout after {timeout}s"
        return RemoteResult(host_id=host.id, rc=124, stdout=out, stderr=err)
    except FileNotFoundError:
        return RemoteResult(
            host_id=host.id,
            rc=127,
            stdout="",
            stderr="ssh binary not found on PATH",
        )
