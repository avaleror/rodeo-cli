"""Run ``rodeo status --output json`` on fleet hosts."""
from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from typing import Any

from .fanout import fanout
from .inventory import FleetHost, FleetInventory
from .ssh_exec import run_remote


@dataclass
class HostStatusResult:
    id: str
    ok: bool
    error: str | None
    report: dict[str, Any] | None


def _status_one(inventory: FleetInventory, host: FleetHost, timeout: float) -> HostStatusResult:
    # Single remote shell string: cd into lab then status. Path is shell-quoted.
    remote_script = (
        f"cd {shlex.quote(inventory.lab_dir)} && rodeo status --output json"
    )
    result = run_remote(
        inventory,
        host,
        ["bash", "-lc", remote_script],
        timeout=timeout,
    )
    if not result.ok:
        err = (result.stderr or result.stdout or f"exit {result.rc}").strip()
        return HostStatusResult(id=host.id, ok=False, error=err, report=None)
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        snippet = result.stdout.strip()[:200]
        return HostStatusResult(
            id=host.id,
            ok=False,
            error=f"invalid JSON from remote status: {exc}; stdout={snippet!r}",
            report=None,
        )
    if not isinstance(report, dict):
        return HostStatusResult(
            id=host.id,
            ok=False,
            error="remote status JSON must be an object",
            report=None,
        )
    return HostStatusResult(id=host.id, ok=True, error=None, report=report)


def fleet_status(
    inventory: FleetInventory,
    hosts: list[FleetHost],
    *,
    concurrency: int = 8,
    timeout: float = 120.0,
) -> list[HostStatusResult]:
    """Fan-out status across ``hosts``."""

    def _work(h: FleetHost) -> HostStatusResult:
        return _status_one(inventory, h, timeout)

    return fanout(hosts, _work, concurrency=concurrency)
