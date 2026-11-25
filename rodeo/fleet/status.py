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
    # The JSON report, then one marker line with the fleet deploy's exit code
    # (empty while it runs or for deploys from before the exit file existed)
    # and whether its tmux session is still alive.
    remote_script = (
        f"cd {shlex.quote(inventory.lab_dir)} && rodeo status --output json && "
        f"printf '\\n{_MARK}%s:%s\\n' "
        f"\"$(cat \"$HOME/.rodeo/logs/fleet-up.exit\" 2>/dev/null)\" "
        f"\"$(tmux ls 2>/dev/null | grep -c '^rodeo-fleet-')\""
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
    body, up_exit, up_running = _split_marker(result.stdout)
    try:
        report = json.loads(body)
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
    if up_exit is not None:
        report["fleet_up_exit"] = up_exit
    if up_running is not None:
        report["fleet_up_running"] = up_running
    return HostStatusResult(id=host.id, ok=True, error=None, report=report)


_MARK = "RODEO_FLEET_UP:"


def _split_marker(stdout: str) -> tuple[str, int | None, bool | None]:
    """JSON body, deploy exit code (None if unknown), deploy session alive."""
    head, sep, tail = stdout.rpartition(_MARK)
    if not sep:
        return stdout, None, None
    code, _, sessions = tail.strip().partition(":")
    up_exit = int(code) if code.strip().isdigit() else None
    running = int(sessions) > 0 if sessions.strip().isdigit() else None
    return head, up_exit, running


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
