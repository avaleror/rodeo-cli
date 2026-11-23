"""``deployment_target: remote``: deploy to a KVM host you already own.

The plan names the host; this machine is only the control plane. The lab's plan files are
shipped over SSH and ``rodeo up`` runs on the host itself as ``baremetal``, reusing the same
bootstrap-and-run path as ``deployment_target: aws`` (:mod:`.remote_up`). Nothing is
provisioned and nothing is destroyed: the host's lifetime is the operator's business.

Plan block::

    deployment_target: remote
    remote_host:
      host: kvm-01.lab.example        # name or IP, required
      ssh_user: root                  # default root
      identity_file: ~/.ssh/id_ed25519  # default: rodeo's managed key
      lab_dir: /root/rodeo-lab        # where the lab lands on the host

``rodeo up --target remote`` needs the host reachable over SSH with passwordless sudo for
*ssh_user*; the builder fills the block from a stored ``kvm-host`` credential.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import ConfigError
from .base import SINGLE_HOST_ID, ProvisionedHost
from .remote_up import run_remote_up

# The log the host writes, and the name of this target.
TARGET = "remote"
LOG_NAME = "remote"
DEFAULT_SSH_USER = "root"


def host_block(cfg: dict[str, Any]) -> dict[str, Any]:
    """The plan's ``remote_host:`` block, or {} when it has none."""
    block = cfg.get("remote_host")
    return dict(block) if isinstance(block, dict) else {}


def deploys_to_remote(cfg: dict[str, Any]) -> bool:
    """True when this plan targets a KVM host over SSH."""
    return str(cfg.get("deployment_target") or "").strip().lower() == TARGET


def ssh_user(cfg: dict[str, Any]) -> str:
    """The login for the host; ``root`` unless the plan says otherwise."""
    return str(host_block(cfg).get("ssh_user") or DEFAULT_SSH_USER)


def identity_file(cfg: dict[str, Any]) -> str | None:
    """The private key to log in with, expanded; None leaves rodeo's managed key."""
    key = host_block(cfg).get("identity_file")
    return str(Path(str(key)).expanduser()) if key else None


def validate(cfg: dict[str, Any]) -> None:
    """Raise ConfigError unless the plan can reach a host.

    Checked before anything is shipped, so a plan that only names the target fails here
    rather than half way through an SSH session.
    """
    block = host_block(cfg)
    if not block:
        raise ConfigError(
            "deployment_target: remote requires a remote_host: block naming the KVM host "
            "(host:, optionally ssh_user:, identity_file:, lab_dir:)"
        )
    if not str(block.get("host") or "").strip():
        raise ConfigError("remote_host: needs a host: (the KVM host's name or IP)")
    key = block.get("identity_file")
    if key and not Path(str(key)).expanduser().is_file():
        raise ConfigError(f"remote_host.identity_file: no such file: {key}")


def host_from_cfg(cfg: dict[str, Any]) -> ProvisionedHost:
    """The operator's host as a one-host :class:`ProvisionedHost`. Validates *cfg* first."""
    validate(cfg)
    name = str(host_block(cfg)["host"]).strip()
    return ProvisionedHost(
        id=SINGLE_HOST_ID,
        ssh=f"{ssh_user(cfg)}@{name}",
        public_ip=name,
        labels={"rodeo_target": TARGET},
    )


def execute_remote_up(
    cfg: dict[str, Any],
    *,
    profile: str | None = None,
    ref: str | None = None,
    remote_timeout: float = 7200.0,
    plan_dir: Path | None = None,
) -> ProvisionedHost:
    """Ship the lab to the plan's KVM host and run ``rodeo up`` there as baremetal."""
    host = host_from_cfg(cfg)
    run_remote_up(
        cfg,
        host,
        profile=profile,
        ref=ref,
        timeout=remote_timeout,
        plan_dir=plan_dir,
        target="baremetal",
        log_name=LOG_NAME,
        ssh_user=ssh_user(cfg),
        identity_file=identity_file(cfg),
        lab_dir=host_block(cfg).get("lab_dir"),
    )
    return host
