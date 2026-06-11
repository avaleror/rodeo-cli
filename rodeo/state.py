"""Persist deploy state in ~/.rodeo/state.yaml."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_STATE_PATH = Path.home() / ".rodeo" / "state.yaml"

PHASES = ["kvm_host", "vms", "cluster", "rancher", "finalise"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict:
    if not _STATE_PATH.exists():
        return {}
    with open(_STATE_PATH) as f:
        return yaml.safe_load(f) or {}


def save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATE_PATH, "w") as f:
        yaml.dump(state, f, default_flow_style=False)


def mark_phase_done(phase: str) -> None:
    state = load_state()
    state.setdefault("phases", {})[phase] = {"completed": True, "timestamp": _now()}
    save_state(state)


def mark_phase_failed(phase: str, error: str) -> None:
    state = load_state()
    state.setdefault("phases", {})[phase] = {
        "completed": False,
        "last_error": error,
        "timestamp": _now(),
    }
    save_state(state)


def reset_phase(phase: str) -> None:
    state = load_state()
    state.get("phases", {}).pop(phase, None)
    save_state(state)


def reset_from(phase: str) -> None:
    """Clear phase and all subsequent phases."""
    state = load_state()
    phases = state.get("phases", {})
    idx = PHASES.index(phase) if phase in PHASES else 0
    for p in PHASES[idx:]:
        phases.pop(p, None)
    state["phases"] = phases
    save_state(state)


def is_phase_done(phase: str) -> bool:
    return load_state().get("phases", {}).get(phase, {}).get("completed", False)
