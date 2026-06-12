"""Persist deploy state in ~/.rodeo/state/<plan-name>.yaml."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

_STATE_DIR = Path.home() / ".rodeo" / "state"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(plan_name: str) -> Path:
    return _STATE_DIR / f"{plan_name}.yaml"


def load_state(plan_name: str = "default") -> dict:
    path = _state_path(plan_name)
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def save_state(state: dict, plan_name: str = "default") -> None:
    path = _state_path(plan_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(state, f, default_flow_style=False)
    path.chmod(0o600)


def mark_phase_done(phase: str, plan_name: str = "default") -> None:
    state = load_state(plan_name)
    state.setdefault("phases", {})[phase] = {"completed": True, "timestamp": _now()}
    save_state(state, plan_name)


def mark_phase_failed(phase: str, error: str, plan_name: str = "default") -> None:
    state = load_state(plan_name)
    state.setdefault("phases", {})[phase] = {
        "completed": False,
        "last_error": error,
        "timestamp": _now(),
    }
    save_state(state, plan_name)


def reset_phase(phase: str, plan_name: str = "default") -> None:
    state = load_state(plan_name)
    state.get("phases", {}).pop(phase, None)
    save_state(state, plan_name)


def reset_from(
    phase: str,
    plan_name: str = "default",
    phases: list[str] | None = None,
) -> None:
    """Clear phase and all subsequent phases.

    phases must be provided (profile.phases); callers (e.g. clean, runner --from) are
    required to pass it. No fallback — raises to catch missing callers during dev/tests.
    """
    if not phases:
        raise ValueError(
            "reset_from(phase, plan_name, phases) requires the phases list "
            "(pass profile.phases). The global default was removed."
        )
    phase_list = phases
    state = load_state(plan_name)
    stored = state.get("phases", {})
    idx = phase_list.index(phase) if phase in phase_list else 0
    for p in phase_list[idx:]:
        stored.pop(p, None)
    state["phases"] = stored
    save_state(state, plan_name)


def is_phase_done(phase: str, plan_name: str = "default") -> bool:
    return load_state(plan_name).get("phases", {}).get(phase, {}).get("completed", False)
