"""Fleet diagnose — pull remote status + log tails for failure forensics."""
from __future__ import annotations

import base64
import io
import json
import re
import shlex
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fanout import fanout
from .inventory import FleetHost, FleetInventory
from .job import FleetJob, job_path_for, load_job
from .ssh_exec import run_remote
from .status import HostStatusResult, fleet_status

_DEFAULT_LINES = 500
_MAX_LINES = 20_000


def diagnose_outdir(inventory_path: Path, explicit: Path | None = None) -> Path:
    """Directory for one diagnose run (beside inventory unless ``-o``)."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    inv = Path(inventory_path).expanduser().resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return inv.parent / f"{inv.stem}.diagnose-{stamp}"


def collect_script(
    inventory: FleetInventory,
    *,
    lines: int = _DEFAULT_LINES,
    tmux_session: str | None = None,
) -> str:
    """Remote bash: status JSON + log/state/tmux tails → base64 tar on stdout."""
    n = max(1, min(int(lines), _MAX_LINES))
    lab = shlex.quote(inventory.lab_dir)
    sess = shlex.quote(tmux_session) if tmux_session else "''"
    # Portable: no GNU find -printf; remote is typically SLES (GNU base64 -w0).
    return (
        "set -euo pipefail; "
        f"N={n}; "
        "TMP=$(mktemp -d); "
        'trap \'rm -rf "$TMP"\' EXIT; '
        'mkdir -p "$TMP/logs" "$TMP/meta"; '
        f"cd {lab} && rodeo status --output json "
        '> "$TMP/status.json" 2> "$TMP/meta/status.stderr" '
        '&& echo 0 > "$TMP/meta/status.rc" '
        '|| { echo $? > "$TMP/meta/status.rc"; true; }; '
        'if [ -f "$HOME/.rodeo/logs/fleet-up.log" ]; then '
        'tail -n "$N" "$HOME/.rodeo/logs/fleet-up.log" '
        '> "$TMP/logs/fleet-up.log"; fi; '
        'if [ -d "$HOME/.rodeo/logs" ]; then '
        'for f in $(ls -t "$HOME/.rodeo/logs"/*.log 2>/dev/null | head -n 6); do '
        'bn=$(basename "$f"); '
        '[ "$bn" = "fleet-up.log" ] && continue; '
        'tail -n "$N" "$f" > "$TMP/logs/$bn"; '
        "done; fi; "
        'if [ -d "$HOME/.rodeo/state" ]; then '
        'mkdir -p "$TMP/meta/state"; '
        'cp -a "$HOME/.rodeo/state"/. "$TMP/meta/state/" 2>/dev/null || true; '
        "fi; "
        f"SESS={sess}; "
        'if [ -n "$SESS" ] && command -v tmux >/dev/null 2>&1 '
        '&& tmux has-session -t "$SESS" 2>/dev/null; then '
        'tmux capture-pane -pt "$SESS" -S -200 '
        '> "$TMP/meta/tmux-pane.txt" 2>/dev/null || true; '
        'printf "%s\\n" "$SESS" > "$TMP/meta/tmux-session.txt"; '
        "fi; "
        'tar czf - -C "$TMP" . | base64 -w0 2>/dev/null '
        '|| tar czf - -C "$TMP" . | base64'
    )


def _safe_members(tf: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Reject path traversal in remote tar members."""
    safe: list[tarfile.TarInfo] = []
    for m in tf.getmembers():
        name = m.name.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            continue
        safe.append(m)
    return safe


def extract_collect_b64(stdout: str, dest: Path) -> list[str]:
    """Decode base64 tar from remote stdout into ``dest``; return member paths."""
    dest.mkdir(parents=True, exist_ok=True)
    payload = re.sub(r"\s+", "", stdout.strip())
    if not payload:
        raise ValueError("empty collect payload")
    raw = base64.b64decode(payload, validate=False)
    names: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        members = _safe_members(tf)
        # filter= is 3.12+; CI also runs 3.10
        try:
            tf.extractall(dest, members=members, filter="data")  # type: ignore[call-arg]
        except TypeError:
            tf.extractall(dest, members=members)
        names = [m.name for m in members if m.isfile()]
    return names


def failed_phases(report: dict[str, Any] | None) -> list[str]:
    if not report:
        return []
    phases = report.get("phases") or {}
    out: list[str] = []
    for name, info in phases.items():
        if isinstance(info, dict) and info.get("last_error"):
            out.append(str(name))
    return out


def phase_error_summary(report: dict[str, Any] | None) -> str | None:
    if not report:
        return None
    phases = report.get("phases") or {}
    parts: list[str] = []
    for name, info in phases.items():
        if isinstance(info, dict) and info.get("last_error"):
            parts.append(f"{name}: {info['last_error']}")
    return "; ".join(parts) if parts else None


def host_needs_forensics(
    status: HostStatusResult,
    *,
    job: FleetJob | None,
) -> bool:
    """True when the host looks failed or stuck with a phase error."""
    if job and status.id in job.failed_ids():
        return True
    if not status.ok:
        return True
    if failed_phases(status.report):
        return True
    return False


@dataclass
class HostDiagnoseResult:
    id: str
    ok: bool  # collect + parse succeeded
    error: str | None
    status_ok: bool
    status_error: str | None
    report: dict[str, Any] | None
    failed_phases: list[str] = field(default_factory=list)
    job_state: str | None = None
    job_error: str | None = None
    artifact_dir: str | None = None
    artifacts: list[str] = field(default_factory=list)
    needs_attention: bool = False


def _tmux_for(job: FleetJob | None, host_id: str) -> str | None:
    if not job:
        return None
    rec = job.hosts.get(host_id)
    return rec.tmux if rec else None


def _diagnose_one(
    inventory: FleetInventory,
    host: FleetHost,
    *,
    host_dir: Path,
    lines: int,
    timeout: float,
    job: FleetJob | None,
) -> HostDiagnoseResult:
    host_dir.mkdir(parents=True, exist_ok=True)
    job_state = None
    job_error = None
    if job and host.id in job.hosts:
        rec = job.hosts[host.id]
        job_state = rec.state
        job_error = rec.last_error

    script = collect_script(
        inventory,
        lines=lines,
        tmux_session=_tmux_for(job, host.id),
    )
    result = run_remote(
        inventory,
        host,
        ["bash", "-lc", script],
        timeout=timeout,
    )
    if not result.ok:
        err = (result.stderr or result.stdout or f"exit {result.rc}").strip()
        return HostDiagnoseResult(
            id=host.id,
            ok=False,
            error=err[:500],
            status_ok=False,
            status_error=err[:500],
            report=None,
            job_state=job_state,
            job_error=job_error,
            artifact_dir=str(host_dir),
            needs_attention=True,
        )

    try:
        artifacts = extract_collect_b64(result.stdout, host_dir)
    except Exception as exc:  # noqa: BLE001 — surface any decode/extract failure
        # Keep raw stdout snippet for debugging the collector itself
        (host_dir / "collect.stdout.txt").write_text(
            (result.stdout or "")[:8000], encoding="utf-8", errors="replace"
        )
        if result.stderr:
            (host_dir / "collect.stderr.txt").write_text(
                result.stderr[:4000], encoding="utf-8", errors="replace"
            )
        return HostDiagnoseResult(
            id=host.id,
            ok=False,
            error=f"failed to extract collect archive: {exc}",
            status_ok=False,
            status_error=str(exc),
            report=None,
            job_state=job_state,
            job_error=job_error,
            artifact_dir=str(host_dir),
            artifacts=["collect.stdout.txt"],
            needs_attention=True,
        )

    report: dict[str, Any] | None = None
    status_path = host_dir / "status.json"
    status_ok = True
    status_error: str | None = None
    if status_path.is_file():
        try:
            report = json.loads(status_path.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                report = None
                status_ok = False
                status_error = "status.json is not an object"
        except json.JSONDecodeError as exc:
            status_ok = False
            status_error = f"invalid status.json: {exc}"
    else:
        status_ok = False
        status_error = "status.json missing from collect archive"
        rc_path = host_dir / "meta" / "status.rc"
        err_path = host_dir / "meta" / "status.stderr"
        if err_path.is_file():
            status_error = err_path.read_text(encoding="utf-8", errors="replace").strip()[
                :500
            ] or status_error
        elif rc_path.is_file():
            status_error = f"status exit {rc_path.read_text().strip()}"

    phases = failed_phases(report)
    summary = {
        "id": host.id,
        "status_ok": status_ok,
        "status_error": status_error,
        "failed_phases": phases,
        "phase_errors": phase_error_summary(report),
        "job_state": job_state,
        "job_error": job_error,
        "vip": (report or {}).get("vip"),
        "vip_reachable": (report or {}).get("vip_reachable"),
        "artifacts": artifacts,
    }
    (host_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    needs = bool(
        phases
        or job_state == "failed"
        or not status_ok
        or job_error
    )
    return HostDiagnoseResult(
        id=host.id,
        ok=True,
        error=None,
        status_ok=status_ok,
        status_error=status_error,
        report=report,
        failed_phases=phases,
        job_state=job_state,
        job_error=job_error,
        artifact_dir=str(host_dir),
        artifacts=artifacts,
        needs_attention=needs,
    )


def select_diagnose_hosts(
    hosts: list[FleetHost],
    *,
    inventory: FleetInventory,
    inventory_path: Path,
    failed_only: bool,
    concurrency: int,
    timeout: float,
) -> tuple[list[FleetHost], FleetJob | None, list[HostStatusResult] | None]:
    """Optionally narrow to failed hosts via job file and/or live status."""
    job: FleetJob | None = None
    job_file = job_path_for(inventory_path)
    if job_file.is_file():
        try:
            job = load_job(job_file)
        except Exception:  # noqa: BLE001
            job = None

    if not failed_only:
        return hosts, job, None

    if job and job.failed_ids():
        failed = set(job.failed_ids())
        selected = [h for h in hosts if h.id in failed]
        if selected:
            return selected, job, None

    # No job failures recorded — probe status and keep hosts with problems.
    status_results = fleet_status(
        inventory, hosts, concurrency=concurrency, timeout=timeout
    )
    by_id = {s.id: s for s in status_results}
    selected = [
        h for h in hosts if host_needs_forensics(by_id[h.id], job=job)
    ]
    return selected, job, status_results


def fleet_diagnose(
    inventory: FleetInventory,
    hosts: list[FleetHost],
    *,
    inventory_path: Path,
    outdir: Path,
    concurrency: int = 8,
    timeout: float = 180.0,
    lines: int = _DEFAULT_LINES,
    job: FleetJob | None = None,
) -> tuple[list[HostDiagnoseResult], Path]:
    """Collect status + logs for ``hosts`` into ``outdir``."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "workshop.json").write_text(
        json.dumps(
            {
                "workshop": inventory.name,
                "lab_dir": inventory.lab_dir,
                "inventory": str(Path(inventory_path).resolve()),
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "host_ids": [h.id for h in hosts],
                "lines": lines,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    def _work(h: FleetHost) -> HostDiagnoseResult:
        return _diagnose_one(
            inventory,
            h,
            host_dir=outdir / h.id,
            lines=lines,
            timeout=timeout,
            job=job,
        )

    results = fanout(hosts, _work, concurrency=concurrency)
    index = diagnose_payload(inventory.name, outdir, results)
    (outdir / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return results, outdir


def diagnose_payload(
    workshop: str,
    outdir: Path,
    results: list[HostDiagnoseResult],
) -> dict[str, Any]:
    return {
        "workshop": workshop,
        "outdir": str(outdir),
        "hosts": [
            {
                "id": r.id,
                "ok": r.ok,
                "error": r.error,
                "status_ok": r.status_ok,
                "status_error": r.status_error,
                "failed_phases": r.failed_phases,
                "phase_errors": phase_error_summary(r.report),
                "job_state": r.job_state,
                "job_error": r.job_error,
                "needs_attention": r.needs_attention,
                "artifact_dir": r.artifact_dir,
                "artifacts": r.artifacts,
                "report": r.report,
            }
            for r in results
        ],
    }
