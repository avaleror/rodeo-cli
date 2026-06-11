"""Shared fixtures: isolated HOME, state dir, and a fake profile."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from rodeo import state
from rodeo.engine.runner import DeployEvent
from rodeo.profiles import _REGISTRY
from rodeo.profiles.base import RodeoProfile


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Keep ~/.rodeo state/vars and secrets inside tmp_path for every test."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("RODEO_PASSWORD", raising=False)
    monkeypatch.setattr(state, "_STATE_DIR", tmp_path / "state")
    return tmp_path


class FakeProfile(RodeoProfile):
    """Three-phase profile with scriptable per-phase results."""

    name = "fake"
    phases = ["alpha", "beta", "gamma"]
    vm_names = ["vm1", "vm2"]
    ansible_phases = frozenset()
    guarded_phases = frozenset(["gamma"])

    def __init__(self) -> None:
        self.results: dict[str, int] = {}      # phase -> rc (default 0)
        self.raises: set[str] = set()          # phases that raise mid-run
        self.ran: list[str] = []

    def default_cfg(self) -> dict:
        return {"vms": {"vm1": {"ip": "10.0.0.1", "user": "root"}}}

    def run_phase(self, phase, runner, vars_file: Path) -> Iterator[DeployEvent]:
        self.ran.append(phase)
        if phase in self.raises:
            raise RuntimeError(f"boom in {phase}")
        runner._last_rc = self.results.get(phase, 0)
        return
        yield  # pragma: no cover — makes this a generator


@pytest.fixture
def fake_profile(monkeypatch):
    profile = FakeProfile()
    monkeypatch.setitem(_REGISTRY, "fake", profile)
    return profile


@pytest.fixture
def fake_cfg():
    return {
        "type": "fake",
        "name": "test-plan",
        "deployment_target": "baremetal",
        "network": {"vip": "10.0.0.10", "rancher_ip": "10.0.0.9"},
        "credentials": {"harvester_os_password": "Secret123", "lab_admin_password": "Secret123"},
        "ansible": {"inventory": "deployer/inventory.local"},
        "vms": {"vm1": {"ip": "10.0.0.1", "user": "root"}},
    }


def drain(gen):
    """Exhaust a generator, returning (events, return_value)."""
    events = []
    while True:
        try:
            events.append(next(gen))
        except StopIteration as exc:
            return events, exc.value
