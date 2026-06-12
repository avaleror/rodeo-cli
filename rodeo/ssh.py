"""Shared SSH options for host-to-VM connections (lab-grade: no host key checks)."""

from __future__ import annotations

_SSH_OPTS: list[str] = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=10",
    "-o", "BatchMode=yes",
    "-o", "LogLevel=ERROR",
]


def ssh_opts() -> list[str]:
    """Return common ssh options for rodeo lab connections."""
    return list(_SSH_OPTS)


def ssh_base(user: str, host: str, key_path: str) -> list[str]:
    """Base ssh argv prefix: ssh -i KEY OPTS user@host"""
    return ["ssh", "-i", key_path, *ssh_opts(), f"{user}@{host}"]