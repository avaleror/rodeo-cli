"""Structured lab status report for ``rodeo status --output json``."""
from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from typing import Any

from ..profiles import get_profile
from ..state import load_state

# Fallback when remote status JSON predates ``no_cache`` on phase entries.
# All bundled profiles put ``apply`` in ``no_cache_phases`` (never marked done).
_DEFAULT_NO_CACHE_PHASES = frozenset({"apply"})


def phase_is_no_cache(name: str, info: dict[str, Any]) -> bool:
    """True when a phase must not block converge / fleet 'complete' checks."""
    if info.get("no_cache"):
        return True
    return name in _DEFAULT_NO_CACHE_PHASES


def cacheable_phases_complete(phases: dict[str, Any] | None) -> bool:
    """Return True when every *cacheable* phase reports completed.

    ``apply`` (and any phase marked ``no_cache``) is re-run every local deploy and
    is never stored as completed — requiring it made fleet skip/ok detection dead.
    """
    if not phases:
        return False
    saw_cacheable = False
    for name, info in phases.items():
        if not isinstance(info, dict):
            return False
        if phase_is_no_cache(name, info):
            continue
        saw_cacheable = True
        if not info.get("completed"):
            return False
    return saw_cacheable


def vip_reachable(vip: str) -> bool:
    """Return True when HTTPS to the VIP answers (any HTTP status counts)."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        urllib.request.urlopen(f"https://{vip}", timeout=5, context=ctx)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def status_report(cfg: dict) -> dict[str, Any]:
    """Return a JSON-serializable status report for the loaded plan."""
    profile = get_profile(cfg.get("type", "suse-virt"))
    plan_name = cfg.get("name", "default")
    state = load_state(plan_name)
    vip = cfg.get("network", {}).get("vip", "")
    no_cache = getattr(profile, "no_cache_phases", frozenset()) or frozenset()

    vm_names = list(cfg.get("vms", {}).keys()) or list(profile.vm_names)
    vms: list[dict[str, Any]] = []
    libvirt_error: str | None = None
    try:
        from ..engine.libvirt import LibvirtDriver

        uri = cfg.get("libvirt", {}).get("uri", "qemu:///system")
        with LibvirtDriver(uri) as lv:
            for vm in lv.list_vms(vm_names):
                vms.append(
                    {
                        "name": vm.name,
                        "state": vm.state,
                        "autostart": bool(vm.autostart),
                    }
                )
    except RuntimeError as exc:
        libvirt_error = str(exc)

    phases_out: dict[str, dict[str, Any]] = {}
    stored = state.get("phases") or {}
    for phase in profile.phases:
        info = stored.get(phase) or {}
        entry: dict[str, Any] = {"completed": bool(info.get("completed"))}
        if phase in no_cache:
            entry["no_cache"] = True
        if info.get("timestamp"):
            entry["timestamp"] = info["timestamp"]
        if info.get("last_error"):
            entry["last_error"] = info["last_error"]
        phases_out[phase] = entry

    report: dict[str, Any] = {
        "name": plan_name,
        "vip": vip,
        "vip_reachable": vip_reachable(vip) if vip else False,
        "vms": vms,
        "phases": phases_out,
        "phases_complete": cacheable_phases_complete(phases_out),
    }
    if libvirt_error:
        report["libvirt_error"] = libvirt_error
    return report
