"""Run ``rodeo doctor --output json`` on fleet hosts."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .fanout import fanout
from .inventory import FleetHost, FleetInventory
from .ssh_exec import run_remote


@dataclass
class HostDoctorResult:
    id: str
    ok: bool
    error: str | None
    report: dict[str, Any] | None


def _readiness_problems(report: dict[str, Any]) -> list[str]:
    """Derive workshop readiness from a doctor report.

    Local ``rodeo doctor`` is a read-only advisory command and never exits
    non-zero on unmet requirements, so a bare SSH+rc==0 check would mark a
    host with no /dev/kvm as "ok". Fleet has to compute pass/fail itself from
    the structured report instead of trusting the remote process's exit code.
    """
    host = report.get("host") or {}
    problems: list[str] = []
    if not host.get("has_kvm"):
        problems.append("no /dev/kvm")
    if not host.get("nested"):
        problems.append("nested virtualization not enabled")
    missing_tools = [t for t, ok in (report.get("core_tools") or {}).items() if not ok]
    if missing_tools:
        problems.append(f"missing tools: {', '.join(sorted(missing_tools))}")
    missing_py = [m for m, ok in (report.get("py_modules") or {}).items() if not ok]
    if missing_py:
        problems.append(f"missing python modules: {', '.join(sorted(missing_py))}")
    if not report.get("profile_fits"):
        problems.append("no bundled profile fits available RAM")
    return problems


def _doctor_one(inventory: FleetInventory, host: FleetHost, timeout: float) -> HostDoctorResult:
    result = run_remote(
        inventory,
        host,
        ["rodeo", "doctor", "--output", "json"],
        timeout=timeout,
    )
    if not result.ok:
        err = (result.stderr or result.stdout or f"exit {result.rc}").strip()
        return HostDoctorResult(id=host.id, ok=False, error=err, report=None)
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        snippet = result.stdout.strip()[:200]
        return HostDoctorResult(
            id=host.id,
            ok=False,
            error=f"invalid JSON from remote doctor: {exc}; stdout={snippet!r}",
            report=None,
        )
    if not isinstance(report, dict):
        return HostDoctorResult(
            id=host.id,
            ok=False,
            error="remote doctor JSON must be an object",
            report=None,
        )
    problems = _readiness_problems(report)
    if problems:
        return HostDoctorResult(id=host.id, ok=False, error="; ".join(problems), report=report)
    return HostDoctorResult(id=host.id, ok=True, error=None, report=report)


def fleet_doctor(
    inventory: FleetInventory,
    hosts: list[FleetHost],
    *,
    concurrency: int = 8,
    timeout: float = 120.0,
) -> list[HostDoctorResult]:
    """Fan-out doctor across ``hosts``."""

    def _work(h: FleetHost) -> HostDoctorResult:
        return _doctor_one(inventory, h, timeout)

    return fanout(hosts, _work, concurrency=concurrency)
