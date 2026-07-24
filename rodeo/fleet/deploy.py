"""Fleet deploy — bootstrap, sync, start remote ``rodeo up`` in tmux."""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bootstrap import bootstrap_script
from .fanout import fanout
from ..service.status import cacheable_phases_complete, phase_is_no_cache
from .inventory import FleetHost, FleetInventory, require_deploy_config
from .job import FleetJob, HostJobRecord, job_path_for, new_job, save_job
from .ssh_exec import run_remote
from .sync import sync_script


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def tmux_session_name(workshop: str, host_id: str) -> str:
    """Safe tmux session name (alphanumeric + hyphen)."""
    raw = f"rodeo-fleet-{workshop}-{host_id}"
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")
    return cleaned[:80] or "rodeo-fleet"


def start_up_script(inventory: FleetInventory, session: str) -> str:
    """Detached tmux running ``rodeo up --yes --no-tmux`` in lab.dir."""
    lab = shlex.quote(inventory.lab_dir)
    target = shlex.quote(inventory.lab_target)
    sess = shlex.quote(session)
    # Profile only when seeding via profile; git labs already have a plan.
    profile_bits = ""
    if inventory.lab_profile and not inventory.lab_source:
        profile_bits = f"--profile {shlex.quote(inventory.lab_profile)} "
    inner = (
        f"cd {lab} && rodeo up --yes --no-tmux {profile_bits}"
        f"--dir {lab} --target {target} "
        f"2>&1 | tee -a \"$HOME/.rodeo/logs/fleet-up.log\"; "
        f'echo FLEET_UP_EXIT:$?'
    )
    # Single-quoted for tmux; escape any single quotes in inner (none expected after quote)
    return (
        f"command -v tmux >/dev/null || {{ echo 'tmux not installed' >&2; exit 1; }}; "
        f"mkdir -p \"$HOME/.rodeo/logs\"; "
        f"if tmux has-session -t {sess} 2>/dev/null; then "
        f'echo "ALREADY_RUNNING:{session}"; '
        f"else "
        f"tmux new-session -d -s {sess} {shlex.quote(inner)}; "
        f'echo "STARTED:{session}"; '
        f"fi"
    )


def deploy_remote_script(inventory: FleetInventory, session: str) -> str:
    """Full per-host recipe: bootstrap → sync → start tmux up."""
    return (
        f"set -euo pipefail; "
        f"{bootstrap_script(inventory)}; "
        f"{sync_script(inventory)}; "
        f"{start_up_script(inventory, session)}"
    )


@dataclass
class HostDeployResult:
    id: str
    ok: bool
    state: str  # running | failed | skipped
    error: str | None
    tmux: str | None
    detail: str | None


def _deploy_one(
    inventory: FleetInventory,
    host: FleetHost,
    *,
    timeout: float,
    force: bool,
) -> HostDeployResult:
    session = tmux_session_name(inventory.name, host.id)

    if not force:
        # Skip hosts whose phases are already all complete
        skip = _maybe_skip_complete(inventory, host, timeout=min(timeout, 90.0))
        if skip is not None:
            return skip

    script = deploy_remote_script(inventory, session)
    result = run_remote(
        inventory,
        host,
        ["bash", "-lc", script],
        timeout=timeout,
    )
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if not result.ok:
        msg = err or out or f"exit {result.rc}"
        return HostDeployResult(
            id=host.id,
            ok=False,
            state="failed",
            error=msg[:500],
            tmux=session,
            detail=None,
        )
    detail = out.splitlines()[-1] if out else "started"
    already = "ALREADY_RUNNING:" in out
    return HostDeployResult(
        id=host.id,
        ok=True,
        state="running",
        error=None,
        tmux=session,
        detail=("already running" if already else detail)[:200],
    )


def _maybe_skip_complete(
    inventory: FleetInventory,
    host: FleetHost,
    *,
    timeout: float,
) -> HostDeployResult | None:
    """Return a skipped result when remote cacheable phases are all completed.

    Ignores ``no_cache`` phases such as ``apply`` (never marked completed).
    """
    lab = shlex.quote(inventory.lab_dir)
    result = run_remote(
        inventory,
        host,
        ["bash", "-lc", f"cd {lab} && rodeo status --output json"],
        timeout=timeout,
    )
    if not result.ok:
        return None
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(report, dict):
        return None
    if report.get("phases_complete") is True or cacheable_phases_complete(
        report.get("phases")
    ):
        return HostDeployResult(
            id=host.id,
            ok=True,
            state="skipped",
            error=None,
            tmux=None,
            detail="all phases already completed",
        )
    return None


def fleet_deploy(
    inventory: FleetInventory,
    hosts: list[FleetHost],
    *,
    inventory_path: Path,
    concurrency: int | None = None,
    timeout: float = 600.0,
    force: bool = False,
    merge_job: FleetJob | None = None,
) -> tuple[list[HostDeployResult], FleetJob, Path]:
    """Fan-out deploy starts; write/update job file beside the inventory."""
    require_deploy_config(inventory)
    workers = concurrency if concurrency is not None else inventory.deploy_concurrency
    path = job_path_for(inventory_path)

    if merge_job is None:
        job = new_job(
            workshop=inventory.name,
            inventory_path=inventory_path,
            concurrency=workers,
            host_ids=[h.id for h in hosts],
        )
    else:
        job = merge_job
        for h in hosts:
            if h.id not in job.hosts:
                job.hosts[h.id] = HostJobRecord(state="pending")

    def _work(h: FleetHost) -> HostDeployResult:
        return _deploy_one(inventory, h, timeout=timeout, force=force)

    results = fanout(hosts, _work, concurrency=workers)
    for r in results:
        if r.state == "skipped":
            job.set_host(
                r.id,
                state="ok",
                finished_at=_now(),
                tmux=r.tmux,
                last_error=None,
                detail=r.detail,
            )
        elif r.ok:
            job.set_host(
                r.id,
                state="running",
                started_at=_now(),
                tmux=r.tmux,
                last_error=None,
                detail=r.detail,
            )
        else:
            job.set_host(
                r.id,
                state="failed",
                finished_at=_now(),
                tmux=r.tmux,
                last_error=r.error,
                detail=r.detail,
            )
    save_job(job, path)
    return results, job, path


def refresh_job_from_status(
    inventory: FleetInventory,
    hosts: list[FleetHost],
    job: FleetJob,
    *,
    inventory_path: Path,
    concurrency: int = 8,
    timeout: float = 120.0,
) -> FleetJob:
    """Update job host states from remote ``rodeo status`` (for retry decisions)."""
    from .status import fleet_status

    status_results = fleet_status(
        inventory, hosts, concurrency=concurrency, timeout=timeout
    )
    for sr in status_results:
        rec = job.hosts.get(sr.id) or HostJobRecord(state="pending")
        if not sr.ok or not sr.report:
            # Only mark failed if we previously started something
            if rec.state in ("running", "pending", "failed"):
                job.set_host(
                    sr.id,
                    state="failed",
                    finished_at=_now(),
                    last_error=sr.error or "status failed",
                )
            continue
        phases = sr.report.get("phases") or {}
        if sr.report.get("phases_complete") is True or cacheable_phases_complete(
            phases
        ):
            job.set_host(
                sr.id,
                state="ok",
                finished_at=_now(),
                last_error=None,
                detail="phases complete",
            )
        else:
            # still converging — ignore errors on no_cache phases (e.g. apply)
            failed_phase = next(
                (
                    name
                    for name, p in phases.items()
                    if isinstance(p, dict)
                    and p.get("last_error")
                    and not phase_is_no_cache(name, p)
                ),
                None,
            )
            if failed_phase:
                err = phases[failed_phase].get("last_error") or failed_phase
                job.set_host(
                    sr.id,
                    state="failed",
                    finished_at=_now(),
                    last_error=f"phase {failed_phase}: {err}",
                )
            else:
                job.set_host(sr.id, state="running", last_error=None)
    save_job(job, job_path_for(inventory_path))
    return job


def results_payload(
    workshop: str,
    results: list[HostDeployResult],
    job_path: Path,
) -> dict[str, Any]:
    return {
        "workshop": workshop,
        "job_file": str(job_path),
        "hosts": [
            {
                "id": r.id,
                "ok": r.ok,
                "state": r.state,
                "error": r.error,
                "tmux": r.tmux,
                "detail": r.detail,
            }
            for r in results
        ],
    }
