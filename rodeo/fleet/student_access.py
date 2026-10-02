"""Open lab UI ports to students (claim portal F5.0).

``provider.student_access: open`` never opens anything by itself: provision
keeps the managed security group operator-only, so the Harvester/Rancher
first-login screens are never on the internet while a deploy is still
running. ``rodeo fleet open-access`` opens the UI ports to ``0.0.0.0/0``
only once every host has finished all phases and its UI passwords pass
:func:`rodeo.secretgen.is_strong_password`. The security group is shared by
the whole workshop, so one host that is not ready blocks the lot.

Passwords never leave the hosts: the check runs remotely and prints one
word per key (strong / weak / missing).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import ConfigError
from ..providers import get_provider
from ..secretgen import STRONG_PASSWORD_MIN_LENGTH
from ..service.status import cacheable_phases_complete
from .fanout import fanout
from .inventory import FleetHost, FleetInventory, require_provider
from .ssh_exec import run_remote
from .status import fleet_status

# UI component -> secrets.yaml key holding the admin password for that login.
_PASSWORD_KEY = {
    "harvester": "harvester_admin_password",
    "rancher": "rancher_admin_password",
}

# Remote scripts run as root through `sudo -n -H bash -lc` when ssh_user is not
# root, so HOME is /root while rodeo keeps its state in the *invoking* user's home
# (rodeo.paths.invoking_home, via SUDO_USER). Mirror that lookup exactly, or the
# scripts read /root/.rodeo and find nothing (live bug 2026-09-30).
SECRETS_PATH_SNIPPET = """
def _secrets_path():
    import os
    user = os.environ.get("SUDO_USER")
    if user:
        try:
            import pwd
            return os.path.join(pwd.getpwnam(user).pw_dir, ".rodeo", "secrets.yaml")
        except KeyError:
            pass
    return os.path.expanduser("~/.rodeo/secrets.yaml")
"""

# Runs on the host with ``python3 -c``; argv = the secrets.yaml keys to check.
# Prints ``key=strong|weak|missing`` per key and never the value. Keep the
# rule in sync with secretgen.is_strong_password (pinned by a test).
PASSWORD_CHECK_SCRIPT = SECRETS_PATH_SNIPPET + f"""
import os, sys
want = sys.argv[1:]
vals = {{}}
try:
    with open(_secrets_path()) as fh:
        for line in fh:
            key, sep, val = line.partition(":")
            if sep and key.strip() in want:
                vals[key.strip()] = val.strip().strip("\\"'")
except OSError:
    pass
def strong(pw):
    return (len(pw) >= {STRONG_PASSWORD_MIN_LENGTH}
            and any(c.isupper() for c in pw)
            and any(c.islower() for c in pw)
            and any(c.isdigit() for c in pw))
for key in want:
    pw = vals.get(key) or ""
    print(key + "=" + ("missing" if not pw else "strong" if strong(pw) else "weak"))
"""


@dataclass(frozen=True)
class HostReadiness:
    id: str
    ready: bool
    reason: str | None  # why not ready; never contains a secret


@dataclass(frozen=True)
class OpenAccessResult:
    workshop: str
    action: str  # opened | closed | refused
    ports: tuple[int, ...]
    security_group: str | None
    hosts: list[HostReadiness]


def student_ports(inventory: FleetInventory) -> dict[str, int]:
    """UI component -> port that open-access exposes (never SSH)."""
    components = inventory.lab_components
    out: dict[str, int] = {}
    if components is None or "harvester" in components:
        out["harvester"] = inventory.harvester_ui_port
    if components is None or "rancher" in components:
        out["rancher"] = inventory.rancher_ui_port
    if not out:
        raise ConfigError(
            "lab.components has neither harvester nor rancher: no UI port to open"
        )
    return out


def _check_host(
    inventory: FleetInventory,
    host: FleetHost,
    keys: list[str],
    *,
    timeout: float,
) -> HostReadiness:
    status = fleet_status(inventory, [host], concurrency=1, timeout=timeout)[0]
    if not status.ok or not status.report:
        return HostReadiness(host.id, False, f"status failed: {status.error or 'no report'}")
    report = status.report
    if not (
        report.get("phases_complete") is True
        or cacheable_phases_complete(report.get("phases"))
    ):
        return HostReadiness(host.id, False, "deploy not finished (phases incomplete)")

    result = run_remote(
        inventory, host, ["python3", "-c", PASSWORD_CHECK_SCRIPT, *keys], timeout=timeout
    )
    if not result.ok:
        # stderr only: stdout of this script is key=word lines, but keep the
        # error path free of anything read from secrets.yaml regardless.
        return HostReadiness(
            host.id, False, f"password check failed (exit {result.rc})"
        )
    verdicts: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, sep, word = line.strip().partition("=")
        if sep and key in keys and word in ("strong", "weak", "missing"):
            verdicts[key] = word
    problems = [f"{k} {verdicts.get(k, 'unchecked')}" for k in keys if verdicts.get(k) != "strong"]
    if problems:
        return HostReadiness(
            host.id,
            False,
            "password not strong enough for the internet: "
            + ", ".join(problems)
            + " (rotate with `rodeo set-password` on the host)",
        )
    return HostReadiness(host.id, True, None)


def check_fleet_ready(
    inventory: FleetInventory,
    *,
    concurrency: int = 8,
    timeout: float = 120.0,
) -> list[HostReadiness]:
    """Readiness of every inventory host for the ports open-access would open."""
    keys = [_PASSWORD_KEY[c] for c in student_ports(inventory)]

    def _work(h: FleetHost) -> HostReadiness:
        return _check_host(inventory, h, keys, timeout=timeout)

    return fanout(inventory.hosts, _work, concurrency=concurrency)


def _provider_for(inventory: FleetInventory) -> tuple[Any, dict[str, Any]]:
    provider_cfg = require_provider(inventory)
    provider = get_provider(str(provider_cfg["type"]))
    if not hasattr(provider, "set_student_access"):
        raise ConfigError(
            f"provider {provider_cfg['type']!r} does not support student access"
        )
    return provider, provider_cfg


def fleet_open_access(
    inventory: FleetInventory,
    *,
    close: bool = False,
    concurrency: int = 8,
    timeout: float = 120.0,
) -> OpenAccessResult:
    """Open (or with ``close``, re-close) the lab UI ports to 0.0.0.0/0."""
    provider, provider_cfg = _provider_for(inventory)
    if close:
        sg = provider.set_student_access(provider_cfg, workshop=inventory.name, open_ports=())
        return OpenAccessResult(inventory.name, "closed", (), sg, [])

    if inventory.student_access != "open":
        raise ConfigError(
            "provider.student_access is not `open` in the inventory: set it "
            "explicitly to allow exposing lab UIs to the internet"
        )
    if not inventory.hosts:
        raise ConfigError("no hosts in the inventory (run `rodeo fleet provision` first)")
    ports = tuple(sorted(set(student_ports(inventory).values())))
    # Claim portal with student SSH (F5.4): key-only :22 for the per-lab
    # unprivileged `student` user. Never opened otherwise.
    student_ssh = bool(inventory.portal and inventory.portal.enabled and inventory.portal.student_ssh)
    if student_ssh:
        ports = (22, *ports)
    readiness = check_fleet_ready(inventory, concurrency=concurrency, timeout=timeout)
    if not all(r.ready for r in readiness):
        return OpenAccessResult(inventory.name, "refused", ports, None, readiness)
    extra = {"allow_ssh": True} if student_ssh else {}
    sg = provider.set_student_access(
        provider_cfg, workshop=inventory.name, open_ports=ports, **extra
    )
    return OpenAccessResult(inventory.name, "opened", ports, sg, readiness)


def open_access_payload(result: OpenAccessResult) -> dict[str, Any]:
    return {
        "workshop": result.workshop,
        "action": result.action,
        "ports": list(result.ports),
        "security_group": result.security_group,
        "hosts": [
            {"id": r.id, "ready": r.ready, "reason": r.reason} for r in result.hosts
        ],
    }
