"""Turn a builder deploy's KVM-host credential into the plan's ``remote_host:`` block.

A rodeo with ``deployment_target: remote`` is deployed to a host the operator owns
(:mod:`rodeo.providers.remote_host`). The page sends that host's details with the deploy —
from a stored ``kvm-host`` credential it has just opened — and this module writes them into
the lab: the private key as a file only root can read, the rest as plan keys.

Nothing here is stored outside the lab directory, and the key file never leaves it.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# The lab-local private key a remote deploy logs in with.
KEY_FILE = ".rodeo-remote-key"
TARGET = "remote"


class NeedRemoteHost(Exception):
    """The plan targets a KVM host but the request carried no host to deploy to."""

    def __init__(self) -> None:
        super().__init__(
            "this rodeo deploys to a KVM host over SSH: pick a stored KVM host credential"
        )


def targets_remote(plan: dict[str, Any]) -> bool:
    """True when *plan* deploys to a KVM host over SSH."""
    return str(plan.get("deployment_target") or "").strip().lower() == TARGET


def _clean(spec: Any) -> dict[str, str]:
    """The usable string fields of a request's ``remote_host``; {} when it has none."""
    if not isinstance(spec, dict):
        return {}
    out = {}
    for name in ("host", "ssh_user", "private_key", "lab_dir"):
        value = spec.get(name)
        if isinstance(value, str) and value.strip():
            out[name] = value if name == "private_key" else value.strip()
    return out


def write_key(lab: Path, private_key: str) -> Path:
    """Write *private_key* into *lab* as a 0600 file and return its path.

    OpenSSH refuses a key file others can read, and refuses one without a trailing newline.
    """
    path = lab / KEY_FILE
    text = private_key if private_key.endswith("\n") else private_key + "\n"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        fh.write(text)
    return path


def apply(lab: Path, plan_path: Path, spec: Any) -> dict[str, str]:
    """Write *spec* into the lab's plan as ``remote_host:``; returns the block written.

    Raises :class:`NeedRemoteHost` when the plan targets a KVM host and neither *spec* nor the
    plan itself names one, and ValueError when *spec* names a host without a key to reach it.
    The plan's own ``remote_host:`` wins for any field *spec* leaves out, so a hand-written
    plan keeps its lab_dir and ssh_user.
    """
    plan = yaml.safe_load(plan_path.read_text()) or {}
    if not targets_remote(plan):
        return {}
    fields = _clean(spec)
    stored = plan.get("remote_host") if isinstance(plan.get("remote_host"), dict) else {}
    host = fields.get("host") or str(stored.get("host") or "")
    if not host:
        raise NeedRemoteHost()
    block: dict[str, str] = {**{str(k): str(v) for k, v in stored.items()}, "host": host}
    if fields.get("ssh_user"):
        block["ssh_user"] = fields["ssh_user"]
    if fields.get("lab_dir"):
        block["lab_dir"] = fields["lab_dir"]
    if fields.get("private_key"):
        block["identity_file"] = str(write_key(lab, fields["private_key"]))
    elif not block.get("identity_file"):
        raise ValueError(
            "the KVM host credential has no private key, and the plan's remote_host: names no "
            "identity_file: add the key to the credential or the plan"
        )
    plan["remote_host"] = block
    plan_path.write_text(yaml.dump(plan, default_flow_style=False, sort_keys=False))
    return block
