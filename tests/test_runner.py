"""DeployRunner event flow, state integration, guard, and vars file."""
from __future__ import annotations

import logging
import stat

import pytest
import yaml

from rodeo import state
from rodeo.config import ConfigError
from rodeo.engine.runner import (
    DeployComplete,
    DeployRunner,
    LogLine,
    PhaseDone,
    PhaseFailed,
    PhaseSkipped,
)


@pytest.fixture(autouse=True)
def _stub_inventory_for_fake_runner_tests(monkeypatch):
    """The fake test profile has no definition.yaml; stub inventory for runner tests."""
    from rodeo import inventory as inv_mod

    real_build = inv_mod.build_inventory

    def _build(cfg):
        if cfg.get("type") == "fake":
            return {}
        return real_build(cfg)

    monkeypatch.setattr(inv_mod, "build_inventory", _build)


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
    fake_cfg["versions"] = {"harvester": "1.8.1"}
    runner = DeployRunner(fake_cfg, tmp_path)
    vars_file = runner._write_vars_file()

    assert stat.S_IMODE(vars_file.stat().st_mode) == 0o600
    data = yaml.safe_load(vars_file.read_text())
    assert data["harvester_os_password"] == "Secret123"
    # Nested structure: this is what roles/vms actually reads.
    assert data["libvirt_flavors"]["harvester"]["memory_mib"] == 4096
    assert data["libvirt_flavors"]["harvester"]["vcpu"] == 2
    assert data["libvirt_flavors"]["rancher"]["memory_mib"] == 8192  # default kept
    assert data["lab_dns_domain"] == "rodeo.lab"
    assert data["harvester_version"] == "1.8.1"
    assert data["harvester_iso_checksum"].startswith("sha512:")
    assert data["libvirt_network_gateway"] == "192.168.122.1"
    # No hardcoded role-default fallback exists anymore — the token must come
    # from credentials (secrets.yaml), verbatim.
    assert data["harvester_token"] == "test-token-123"
    # Flat per-flavor keys are not consumed by Ansible — must not reappear.
    assert "harvester_memory_mb" not in data
    assert "dns_domain" not in data


def test_vars_file_unknown_version_disables_checksum(fake_profile, fake_cfg, tmp_path):
    fake_cfg["versions"] = {"harvester": "9.9.9"}
    vars_file = DeployRunner(fake_cfg, tmp_path)._write_vars_file()
    data = yaml.safe_load(vars_file.read_text())
    # Empty string overrides the 1.8.1 role default so get_url skips the check
    # instead of failing the download against the wrong checksum.
    assert data["harvester_iso_checksum"] == ""


def test_vars_file_passes_token_when_set(fake_profile, fake_cfg, tmp_path):
    fake_cfg["network"]["gateway"] = "10.0.0.1"
    fake_cfg["credentials"]["harvester_token"] = "tok-abc123"
    vars_file = DeployRunner(fake_cfg, tmp_path)._write_vars_file()
    data = yaml.safe_load(vars_file.read_text())
    assert data["harvester_token"] == "tok-abc123"
    assert data["libvirt_network_gateway"] == "10.0.0.1"


def test_stale_vars_files_are_swept(fake_profile, fake_cfg, tmp_path):
    rodeo_dir = tmp_path / ".rodeo"
    rodeo_dir.mkdir()
    stale = rodeo_dir / "rodeo-vars-stale.yaml"
    stale.write_text("old: true\n")
    DeployRunner(fake_cfg, tmp_path)._write_vars_file()
    assert not stale.exists()


def test_reconcile_reruns_vms_on_memory_drift(fake_profile, fake_cfg, tmp_path, monkeypatch):
    """Default reconcile + VM memory drift clears vms cache; unrelated done phases stay skipped."""
    from rodeo.drift import DriftReport, VmChange

    fake_profile.phases = ["alpha", "vms", "gamma"]
    for p in fake_profile.phases:
        state.mark_phase_done(p, "test-plan")

    monkeypatch.setattr(
        "rodeo.drift.collect_drift",
        lambda cfg, actual=None: DriftReport(
            reachable=True,
            vm_changes=[
                VmChange(
                    name="vm1",
                    kind="memory",
                    desired="16384 MiB / 8 vcpu",
                    from_value=8192,
                    to_value=16384,
                )
            ],
        ),
    )

    events = _events(DeployRunner(fake_cfg, tmp_path, reconcile=True))
    assert "vms" in fake_profile.ran
    assert "gamma" in fake_profile.ran
    # alpha stays completed and is skipped (reset_from starts at vms)
    skipped_done = [e for e in _of_type(events, PhaseSkipped) if e.reason == "done"]
    assert [e.phase for e in skipped_done] == ["alpha"]
    assert any("drift:" in e.line for e in _of_type(events, LogLine))
    assert any("--reconcile" in e.line for e in _of_type(events, LogLine))


def test_default_reconcile_consults_drift(fake_profile, fake_cfg, tmp_path, monkeypatch):
    """DeployRunner defaults reconcile=True so drift is checked without an opt-in flag."""
    from rodeo.drift import DriftReport, VmChange

    fake_profile.phases = ["vms"]
    state.mark_phase_done("vms", "test-plan")
    called = {"n": 0}

    def drift(cfg, actual=None):
        called["n"] += 1
        return DriftReport(
            reachable=True,
            vm_changes=[
                VmChange(name="vm1", kind="memory", desired="x", from_value=1, to_value=2)
            ],
        )

    monkeypatch.setattr("rodeo.drift.collect_drift", drift)
    _events(DeployRunner(fake_cfg, tmp_path))
    assert called["n"] == 1
    assert "vms" in fake_profile.ran


def test_without_reconcile_skips_despite_drift(fake_profile, fake_cfg, tmp_path, monkeypatch):
    """--no-reconcile keeps phase-cache-only behaviour even when live memory drifts."""
    from rodeo.drift import DriftReport, VmChange

    fake_profile.phases = ["alpha", "vms", "gamma"]
    for p in fake_profile.phases:
        state.mark_phase_done(p, "test-plan")

    called = {"n": 0}

    def boom(cfg, actual=None):
        called["n"] += 1
        return DriftReport(
            reachable=True,
            vm_changes=[
                VmChange(name="vm1", kind="memory", desired="x", from_value=1, to_value=2)
            ],
        )

    monkeypatch.setattr("rodeo.drift.collect_drift", boom)

    events = _events(DeployRunner(fake_cfg, tmp_path, reconcile=False))
    assert called["n"] == 0  # drift not consulted with reconcile=False
    assert fake_profile.ran == []
    skipped = [e for e in _of_type(events, PhaseSkipped) if e.reason == "done"]
    assert [e.phase for e in skipped] == ["alpha", "vms", "gamma"]


def test_write_vars_file_raises_on_config_error(fake_profile, fake_cfg, tmp_path, monkeypatch):
    def _raise(_cfg):
        raise ConfigError("bad topology")

    monkeypatch.setattr("rodeo.inventory.build_inventory", _raise)
    with pytest.raises(ConfigError, match="bad topology"):
        DeployRunner(fake_cfg, tmp_path)._write_vars_file()


def test_write_vars_file_fails_loud_on_missing_harvester_os_password(fake_profile, fake_cfg, tmp_path):
    """No hardcoded role-default password exists anymore — a plan missing this
    credential must fail the deploy, not silently fall back to a committed secret."""
    del fake_cfg["credentials"]["harvester_os_password"]
    with pytest.raises(RuntimeError, match="harvester_os_password"):
        DeployRunner(fake_cfg, tmp_path)._write_vars_file()


def test_write_vars_file_fails_loud_on_missing_harvester_token(fake_profile, fake_cfg, tmp_path):
    del fake_cfg["credentials"]["harvester_token"]
    with pytest.raises(RuntimeError, match="harvester_token"):
        DeployRunner(fake_cfg, tmp_path)._write_vars_file()


def test_write_vars_file_fails_loud_on_missing_rancher_vm_password(fake_profile, fake_cfg, tmp_path):
    """rancher_vm_password falls back to harvester_os_password when unset — but a
    profile with a rancher/eib VM and neither key present must still fail loud."""
    fake_cfg["vms"] = {"rancher": {"ip": "10.0.0.9", "user": "root"}}
    del fake_cfg["credentials"]["harvester_os_password"]
    fake_cfg["credentials"]["harvester_token"] = ""  # not needed — no harvester nodes
    with pytest.raises(RuntimeError, match="rancher_vm_password"):
        DeployRunner(fake_cfg, tmp_path)._write_vars_file()


def test_write_vars_file_uses_rancher_vm_password_when_set_directly(fake_profile, fake_cfg, tmp_path):
    """suse-edge sets rancher_vm_password without harvester_os_password — the real
    value must flow through, not get discarded in favor of harvester_os_password."""
    fake_cfg["vms"] = {"rancher": {"ip": "10.0.0.9", "user": "root"}, "eib": {"ip": "10.0.0.20", "user": "root"}}
    fake_cfg["credentials"] = {"rancher_vm_password": "EdgeSecret123", "rancher_admin_password": "Secret123"}
    data = yaml.safe_load(DeployRunner(fake_cfg, tmp_path)._write_vars_file().read_text())
    assert data["rancher_vm_password"] == "EdgeSecret123"


def test_write_vars_file_warns_on_unexpected_inventory_error(
    fake_profile, fake_cfg, tmp_path, monkeypatch, caplog,
):
    def _raise(_cfg):
        raise RuntimeError("transient render failure")

    monkeypatch.setattr("rodeo.inventory.build_inventory", _raise)
    with caplog.at_level(logging.WARNING):
        vars_file = DeployRunner(fake_cfg, tmp_path)._write_vars_file()
    assert "using role defaults" in caplog.text
    data = yaml.safe_load(vars_file.read_text())
    assert "vm_nodes" not in data


def test_disk_driver_defaults_baremetal(fake_profile, fake_cfg, tmp_path):
    fake_cfg["deployment_target"] = "baremetal"
    data = yaml.safe_load(DeployRunner(fake_cfg, tmp_path)._write_vars_file().read_text())
    assert data["libvirt_disk_cache"] == "none"
    assert data["libvirt_disk_io"] == "native"


def test_disk_driver_defaults_instruqt(fake_profile, fake_cfg, tmp_path):
    fake_cfg["deployment_target"] = "instruqt"
    data = yaml.safe_load(DeployRunner(fake_cfg, tmp_path)._write_vars_file().read_text())
    assert data["libvirt_disk_cache"] == "writeback"
    assert data["libvirt_disk_io"] == "threads"


def test_disk_driver_plan_override(fake_profile, fake_cfg, tmp_path):
    fake_cfg["deployment_target"] = "instruqt"
    fake_cfg["libvirt"] = {
        "uri": "qemu:///system",
        "disk_cache": "none",
        "disk_io": "native",
    }
    data = yaml.safe_load(DeployRunner(fake_cfg, tmp_path)._write_vars_file().read_text())
    assert data["libvirt_disk_cache"] == "none"
    assert data["libvirt_disk_io"] == "native"
