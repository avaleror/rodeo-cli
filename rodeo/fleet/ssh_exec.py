"""Laptop → KVM-host SSH via OpenSSH subprocess (fleet control plane).

Separate from ``rodeo.ssh`` which is for host→VM lab connections.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
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


def ssh_argv(
    inventory: FleetInventory,
    host: FleetHost,
    remote_command: str,
) -> list[str]:
    """Build OpenSSH argv to run ``remote_command`` on ``host``."""
    argv = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=15",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
    ]
    if inventory.identity_file:
        argv.extend(["-i", inventory.identity_file])
    for opt in inventory.ssh_options:
        # Allow either full "-o Foo=bar" pieces or bare "Foo=bar"
        if opt.startswith("-"):
            argv.append(opt)
        else:
            argv.extend(["-o", opt])
    argv.append(_target(host, inventory))
    argv.append(remote_command)
    return argv


def run_remote(
    inventory: FleetInventory,
    host: FleetHost,
    argv: Sequence[str],
    *,
    timeout: float = 120.0,
) -> RemoteResult:
    """Run ``argv`` on the remote host (joined with shlex) via OpenSSH.

    ``argv`` is the remote command tokens (e.g. ``[\"rodeo\", \"doctor\", \"--output\", \"json\"]``).
    """
    remote_command = " ".join(shlex.quote(a) for a in argv)
    cmd = ssh_argv(inventory, host, remote_command)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
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
