"""start/stop/restart for lab-in-a-box labs whose VMs live in a cloud account.

Those VMs aren't libvirt domains: the day-2 commands hand them to
lab-in-a-box's vm_power.py (rodeo/labinabox_host.py:cloud_power) instead.
"""
from __future__ import annotations

import time

from rich.console import Console


def is_cloud_lab(cfg: dict | None) -> bool:
    if not cfg or cfg.get("type") != "lab-in-a-box":
        return False
    from ..labinabox_host import cloud

    return cloud(cfg) is not None


def _print(console: Console, verb: str, states: dict[str, str]) -> bool:
    ok = True
    for name, state in states.items():
        bad = state.startswith("error:") or state in ("unsupported", "not found")
        ok = ok and not bad
        mark = "[yellow]⚠[/yellow]" if bad else "[dim]" + verb + "[/dim]"
        console.print(f"  {mark} {name}: {state}")
    return ok


def power(cfg: dict, action: str, vm_names: list[str], console: Console,
          sleep=time.sleep, wait_s: int = 300) -> bool:
    """start | stop | restart the lab's cloud VMs. Returns True when every VM answered."""
    from ..labinabox_host import cloud_power

    if action == "restart":
        if not _print(console, "stop", cloud_power(cfg, "stop", vm_names)):
            return False
        deadline = time.monotonic() + wait_s
        states = cloud_power(cfg, "status", vm_names)
        while any(s != "stopped" for s in states.values()) and time.monotonic() < deadline:
            sleep(10)
            states = cloud_power(cfg, "status", vm_names)
        action = "start"
    return _print(console, action, cloud_power(cfg, action, vm_names))
