"""Laptop-side fleet job state (workshop.job.yaml next to inventory)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

from ..config import ConfigError

HostState = Literal["pending", "running", "ok", "failed", "skipped"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class HostJobRecord:
    state: HostState
    started_at: str | None = None
    finished_at: str | None = None
    tmux: str | None = None
    last_error: str | None = None
    detail: str | None = None


@dataclass
class FleetJob:
    workshop: str
    inventory_path: str
    started_at: str
    concurrency: int
    hosts: dict[str, HostJobRecord] = field(default_factory=dict)
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workshop": self.workshop,
            "inventory_path": self.inventory_path,
            "started_at": self.started_at,
            "updated_at": self.updated_at or self.started_at,
            "concurrency": self.concurrency,
            "hosts": {
                hid: {
                    "state": rec.state,
                    "started_at": rec.started_at,
                    "finished_at": rec.finished_at,
                    "tmux": rec.tmux,
                    "last_error": rec.last_error,
                    "detail": rec.detail,
                }
                for hid, rec in self.hosts.items()
            },
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FleetJob:
        hosts: dict[str, HostJobRecord] = {}
        for hid, entry in (raw.get("hosts") or {}).items():
            if not isinstance(entry, dict):
                continue
            hosts[str(hid)] = HostJobRecord(
                state=entry.get("state") or "pending",  # type: ignore[arg-type]
                started_at=entry.get("started_at"),
                finished_at=entry.get("finished_at"),
                tmux=entry.get("tmux"),
                last_error=entry.get("last_error"),
                detail=entry.get("detail"),
            )
        return cls(
            workshop=str(raw.get("workshop") or ""),
            inventory_path=str(raw.get("inventory_path") or ""),
            started_at=str(raw.get("started_at") or _now()),
            concurrency=int(raw.get("concurrency") or 4),
            hosts=hosts,
            updated_at=raw.get("updated_at"),
        )

    def failed_ids(self) -> list[str]:
        return [hid for hid, rec in self.hosts.items() if rec.state == "failed"]

    def set_host(self, host_id: str, **kwargs: Any) -> None:
        rec = self.hosts.get(host_id) or HostJobRecord(state="pending")
        for key, val in kwargs.items():
            setattr(rec, key, val)
        self.hosts[host_id] = rec
        self.updated_at = _now()


def job_path_for(inventory_path: Path) -> Path:
    """``workshop.yaml`` → ``workshop.job.yaml`` beside the inventory."""
    p = Path(inventory_path).expanduser().resolve()
    return p.with_name(p.stem + ".job.yaml")


def save_job(job: FleetJob, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    job.updated_at = _now()
    path.write_text(yaml.dump(job.to_dict(), default_flow_style=False, sort_keys=False))
    path.chmod(0o600)


def load_job(path: Path) -> FleetJob:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise ConfigError(f"Fleet job file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid job YAML ({p}): {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Job file must be a mapping: {p}")
    return FleetJob.from_dict(raw)


def new_job(
    *,
    workshop: str,
    inventory_path: Path,
    concurrency: int,
    host_ids: list[str],
) -> FleetJob:
    job = FleetJob(
        workshop=workshop,
        inventory_path=str(Path(inventory_path).resolve()),
        started_at=_now(),
        concurrency=concurrency,
        hosts={hid: HostJobRecord(state="pending") for hid in host_ids},
    )
    return job
