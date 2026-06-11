"""DeployRunner event flow, state integration, guard, and vars file."""
from __future__ import annotations

import stat

import yaml

from rodeo import state
from rodeo.engine.runner import (
    DeployComplete,
    DeployRunner,
    LogLine,
    PhaseDone,
    PhaseFailed,
    PhaseSkipped,
)


def _events(runner):
    return list(runner.run())


def _of_type(events, typ):
    return [e for e in events if isinstance(e, typ)]


def test_happy_path_runs_all_phases(fake_profile, fake_cfg, tmp_path):
    runner = DeployRunner(fake_cfg, tmp_path)
    events = _events(runner)

    assert fake_profile.ran == ["alpha", "beta", "gamma"]
    assert [e.phase for e in _of_type(events, PhaseDone)] == ["alpha", "beta", "gamma"]
    assert isinstance(events[-1], DeployComplete)
    for phase in fake_profile.phases:
        assert state.is_phase_done(phase, "test-plan")


def test_failure_stops_pipeline_and_marks_state(fake_profile, fake_cfg, tmp_path):
    fake_profile.results["beta"] = 2
    runner = DeployRunner(fake_cfg, tmp_path)
    events = _events(runner)

    assert fake_profile.ran == ["alpha", "beta"]  # gamma never ran
    failed = _of_type(events, PhaseFailed)
    assert len(failed) == 1 and failed[0].phase == "beta" and failed[0].rc == 2
    assert not _of_type(events, DeployComplete)
    assert state.is_phase_done("alpha", "test-plan")
    assert not state.is_phase_done("beta", "test-plan")
    assert state.load_state("test-plan")["phases"]["beta"]["last_error"]


def test_phase_exception_becomes_phase_failed(fake_profile, fake_cfg, tmp_path):
    """Regression for F1: exceptions must not escape the runner."""
    fake_profile.raises.add("beta")
    runner = DeployRunner(fake_cfg, tmp_path)
    events = _events(runner)  # must not raise

    failed = _of_type(events, PhaseFailed)
    assert len(failed) == 1 and failed[0].phase == "beta"
    assert any("boom in beta" in e.line for e in _of_type(events, LogLine))


def test_done_phases_skipped_unless_forced(fake_profile, fake_cfg, tmp_path):
    state.mark_phase_done("alpha", "test-plan")
    events = _events(DeployRunner(fake_cfg, tmp_path))
    skipped = [e for e in _of_type(events, PhaseSkipped) if e.reason == "done"]
    assert [e.phase for e in skipped] == ["alpha"]
    assert fake_profile.ran == ["beta", "gamma"]

    fake_profile.ran.clear()
    _events(DeployRunner(fake_cfg, tmp_path, force=True))
    assert fake_profile.ran == ["alpha", "beta", "gamma"]


def test_from_phase_resets_and_skips_earlier(fake_profile, fake_cfg, tmp_path):
    for p in fake_profile.phases:
        state.mark_phase_done(p, "test-plan")
    events = _events(DeployRunner(fake_cfg, tmp_path, from_phase="beta"))

    before = [e for e in _of_type(events, PhaseSkipped) if e.reason == "before_start"]
    assert [e.phase for e in before] == ["alpha"]
    assert fake_profile.ran == ["beta", "gamma"]
    assert state.is_phase_done("alpha", "test-plan")


def test_instruqt_guard_skips_guarded_phase(fake_profile, fake_cfg, tmp_path):
    fake_cfg["deployment_target"] = "instruqt"
    events = _events(DeployRunner(fake_cfg, tmp_path))

    assert fake_profile.ran == ["alpha", "beta"]  # gamma guarded
    guarded = [e for e in _of_type(events, PhaseSkipped) if e.reason == "instruqt"]
    assert [e.phase for e in guarded] == ["gamma"]
    assert not state.is_phase_done("gamma", "test-plan")
    assert isinstance(events[-1], DeployComplete)


def test_instruqt_guard_bypassed_with_include_guarded(fake_profile, fake_cfg, tmp_path):
    fake_cfg["deployment_target"] = "instruqt"
    _events(DeployRunner(fake_cfg, tmp_path, include_guarded=True))
    assert fake_profile.ran == ["alpha", "beta", "gamma"]


def test_stop_before_phase_cancels(fake_profile, fake_cfg, tmp_path):
    runner = DeployRunner(fake_cfg, tmp_path)
    runner.stop.set()
    events = _events(runner)

    assert fake_profile.ran == []
    failed = _of_type(events, PhaseFailed)
    assert len(failed) == 1 and failed[0].rc == 130


def test_vars_file_wires_plan_and_is_private(fake_profile, fake_cfg, tmp_path):
    fake_cfg["resources"] = {"harvester": {"memory_mib": 4096, "vcpu": 2, "disk_gb": 100}}
    fake_cfg["versions"] = {"harvester": "1.9.0"}
    runner = DeployRunner(fake_cfg, tmp_path)
    vars_file = runner._write_vars_file()

    assert stat.S_IMODE(vars_file.stat().st_mode) == 0o600
    data = yaml.safe_load(vars_file.read_text())
    assert data["harvester_os_password"] == "Secret123"
    assert data["harvester_memory_mb"] == 4096
    assert data["harvester_version"] == "1.9.0"


def test_stale_vars_files_are_swept(fake_profile, fake_cfg, tmp_path):
    rodeo_dir = tmp_path / ".rodeo"
    rodeo_dir.mkdir()
    stale = rodeo_dir / "rodeo-vars-stale.yaml"
    stale.write_text("old: true\n")
    DeployRunner(fake_cfg, tmp_path)._write_vars_file()
    assert not stale.exists()
