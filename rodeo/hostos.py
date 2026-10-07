"""Can this machine run labs itself (KVM host), or only drive remote hosts?

rodeo's Ansible roles, OVMF paths and libvirt setup are written for SLES 16 /
Leap 16. Any other machine is a control plane: it can run `rodeo up --target
aws`, `rodeo fleet ...` and `rodeo destroy --cloud`, but a local deploy would
fail halfway (or, on macOS, not start at all). install.sh makes the same call
when it picks the kvm-host or control-plane install.
"""
from __future__ import annotations

import os
import platform
from pathlib import Path

KVM_HOST_IDS = frozenset({"sles", "sles_sap", "opensuse-leap"})
KVM_HOST_MIN_MAJOR = 16
# Escape hatch for a KVM host on another distribution, at the operator's risk.
ALLOW_ENV = "RODEO_ALLOW_ANY_KVM_HOST"


def _os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def kvm_host_problem(
    *,
    system: str | None = None,
    os_release: dict[str, str] | None = None,
) -> str:
    """'' when this machine can host labs, else a one-line reason why not."""
    if os.environ.get(ALLOW_ENV, "").strip() in ("1", "true", "yes"):
        return ""
    system = system or platform.system()
    if system != "Linux":
        name = "macOS" if system == "Darwin" else system
        return f"{name} cannot host labs (they need Linux with KVM and libvirt)"
    rel = os_release if os_release is not None else _os_release()
    os_id = rel.get("ID", "")
    major = rel.get("VERSION_ID", "").split(".", 1)[0]
    if os_id in KVM_HOST_IDS and major.isdigit() and int(major) >= KVM_HOST_MIN_MAJOR:
        return ""
    pretty = rel.get("PRETTY_NAME") or "this Linux"
    return f"{pretty} is not a supported KVM host (labs run on SLES 16 / Leap 16)"


def control_plane_hint() -> str:
    """What to do instead, for the error message."""
    return (
        "This machine can still drive labs on a remote host:\n"
        "  rodeo up --profile <name> --target aws\n"
        f"(Or set {ALLOW_ENV}=1 to try a local lab anyway, unsupported.)"
    )
