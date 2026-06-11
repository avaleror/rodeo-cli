"""Per-plan state persistence."""
from __future__ import annotations

import stat

from rodeo import state


def test_mark_done_and_query():
    state.mark_phase_done("kvm_host", "plan-a")
    assert state.is_phase_done("kvm_host", "plan-a")
    assert not state.is_phase_done("vms", "plan-a")


def test_plans_are_isolated():
    state.mark_phase_done("kvm_host", "plan-a")
    assert not state.is_phase_done("kvm_host", "plan-b")


def test_mark_failed_records_error():
    state.mark_phase_failed("cluster", "vip timeout", "plan-a")
    assert not state.is_phase_done("cluster", "plan-a")
    s = state.load_state("plan-a")
    assert s["phases"]["cluster"]["last_error"] == "vip timeout"


def test_reset_from_clears_subsequent_only():
    for p in state.PHASES:
        state.mark_phase_done(p, "plan-a")
    state.reset_from("cluster", "plan-a")
    assert state.is_phase_done("kvm_host", "plan-a")
    assert state.is_phase_done("vms", "plan-a")
    for p in ("cluster", "rancher", "finalise"):
        assert not state.is_phase_done(p, "plan-a")


def test_reset_from_custom_phase_list():
    for p in ("alpha", "beta", "gamma"):
        state.mark_phase_done(p, "plan-x")
    state.reset_from("beta", "plan-x", ["alpha", "beta", "gamma"])
    assert state.is_phase_done("alpha", "plan-x")
    assert not state.is_phase_done("beta", "plan-x")
    assert not state.is_phase_done("gamma", "plan-x")


def test_state_file_is_private():
    state.mark_phase_done("kvm_host", "plan-a")
    mode = stat.S_IMODE(state._state_path("plan-a").stat().st_mode)
    assert mode == 0o600
