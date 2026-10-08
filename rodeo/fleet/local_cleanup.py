"""Remove what the laptop kept for cloud hosts once they are terminated.

Each cloud lab leaves files on the control-plane machine: its host key in the
workshop's ``known_hosts``, per-lab student SSH keys, its entry in
``workshop.yaml`` ``hosts[]``, the fleet job file. After a successful
terminate they only point at instances that no longer exist (and a new
instance can reuse the IP), so they are removed with them.

Only what rodeo generated for those instances goes: BYO hosts in ``hosts[]``,
the rest of ``workshop.yaml``, plans, ``secrets.yaml`` and the managed SSH key
are never touched. A host whose terminate failed keeps everything.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

from .ssh_exec import forget_host_key, known_hosts_path

# Deprovision result ids that are not lab hosts.
_NOT_HOSTS = frozenset({"portal", "security-group", "portal-security-group"})


def workshop_state_dir(workshop: str) -> Path:
    """``~/.rodeo/fleet/<workshop>/``: known_hosts and student-keys."""
    return known_hosts_path(workshop).parent


def _is_provisioned(entry: dict[str, Any]) -> bool:
    """A hosts[] entry rodeo created in the cloud (not a BYO machine)."""
    labels = entry.get("labels") or {}
    return isinstance(labels, dict) and bool(labels.get("provider_id"))


def forget_cloud_hosts(
    workshop: str,
    inventory_path: Path,
    results: list[Any],
    *,
    portal_removed: bool,
) -> list[str]:
    """Drop local leftovers of every host whose terminate succeeded.

    ``results`` are the provider's DeprovisionResults. When no host at all is
    left and the portal is gone too, the whole workshop state directory and
    the job file go. Returns one line per thing removed.
    """
    p = Path(inventory_path).expanduser().resolve()
    raw = yaml.safe_load(p.read_text()) or {}
    hosts = raw.get("hosts") or []
    if not isinstance(raw, dict) or not isinstance(hosts, list):
        return []
    ok = [r for r in results if getattr(r, "ok", False)]
    failed = [r for r in results if not getattr(r, "ok", False)]
    gone_ids = {r.id for r in ok if r.id not in _NOT_HOSTS}
    all_gone = any(r.id == "*" for r in ok)  # provider: no instance left at all

    keep: list[Any] = []
    removed: list[dict[str, Any]] = []
    for entry in hosts:
        if (isinstance(entry, dict) and _is_provisioned(entry)
                and (all_gone or str(entry.get("id")) in gone_ids)):
            removed.append(entry)
        else:
            keep.append(entry)

    notes: list[str] = []
    keys_dir = workshop_state_dir(workshop) / "student-keys"
    for entry in removed:
        hid = str(entry.get("id"))
        for addr in {str(entry.get("public_ip") or ""), str(entry.get("ssh") or "")}:
            forget_host_key(workshop, addr)
        for f in (keys_dir / hid, keys_dir / f"{hid}.pub"):
            f.unlink(missing_ok=True)
        notes.append(f"{hid}: removed from {p.name}, its host key and student key")
    if removed:
        raw["hosts"] = keep
        p.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))

    portal = raw.get("portal") if isinstance(raw.get("portal"), dict) else {}
    portal_running = bool(portal.get("public_ip")) and not portal_removed
    # BYO hosts still need known_hosts and the job file, so only an empty
    # hosts[] lets the whole workshop state go.
    if not failed and not keep and not portal_running:
        state = workshop_state_dir(workshop)
        if state.is_dir():
            shutil.rmtree(state)
            notes.append(f"removed {state}")
        from .job import job_path_for

        job = job_path_for(p)
        if job.is_file():
            job.unlink()
            notes.append(f"removed {job.name}")
    return notes


def forget_single_host(plan_name: str) -> list[str]:
    """After ``rodeo destroy --cloud``: the plan's known_hosts directory."""
    state = workshop_state_dir(plan_name)
    if state.is_dir():
        shutil.rmtree(state)
        return [f"removed {state}"]
    return []
