"""Every phase-selecting input rejects an unknown phase name with the valid list."""
from __future__ import annotations

from pathlib import Path

import pytest

from rodeo import state
from rodeo.commands import deploy as deploy_mod
from rodeo.config import ConfigError
from rodeo.engine.runner import DeployRunner
from rodeo.profiles import get_profile


@pytest.fixture(autouse=True)
def _stub_inventory(monkeypatch):
    from rodeo import inventory as inv_mod

    real_build = inv_mod.build_inventory
    monkeypatch.setattr(inv_mod, "build_inventory",
                        lambda cfg: {} if cfg.get("type") == "fake" else real_build(cfg))


# --- execute_deploy(from_phase=...) (shared by deploy and up) ----------------

def test_execute_deploy_rejects_unknown_from_phase(fake_profile, fake_cfg, tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(deploy_mod, "_deploy_plain", lambda *a, **k: called.append(1) or 0)
    with pytest.raises(ConfigError, match=r"Unknown phase 'bogus'\. Valid phases: alpha, beta, gamma"):
        deploy_mod.execute_deploy(fake_cfg, tmp_path, from_phase="bogus", tui=False)
    assert called == []


def test_execute_deploy_accepts_valid_from_phase(fake_profile, fake_cfg, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(deploy_mod, "_deploy_plain", lambda cfg, root, fp, *a, **k: seen.append(fp) or 0)
    monkeypatch.setattr(deploy_mod, "render_success", lambda cfg: None)
    monkeypatch.setattr("rodeo.host_context.persist_host_context_notes", lambda *a, **k: None)
    assert deploy_mod.execute_deploy(fake_cfg, tmp_path, from_phase="beta", tui=False) == 0
    assert seen == ["beta"]


def test_unknown_from_phase_exits_non_zero_via_cli_group(fake_profile, fake_cfg, tmp_path, monkeypatch):
    """ConfigError from a phase check becomes exit 1 with the valid list, not a traceback."""
    import click
    from click.testing import CliRunner

    from rodeo.cli import _RodeoGroup

    @click.group(cls=_RodeoGroup)
    def grp():
        pass

    @grp.command()
    def go():
        deploy_mod.execute_deploy(fake_cfg, tmp_path, from_phase="bogus", tui=False)

    result = CliRunner().invoke(grp, ["go"])
    assert result.exit_code == 1
    assert "Unknown phase 'bogus'" in result.output
    assert "alpha, beta, gamma" in result.output


# --- DeployRunner(from_phase=...) -------------------------------------------

def test_runner_rejects_unknown_from_phase_without_wiping_state(fake_profile, fake_cfg, tmp_path):
    for p in fake_profile.phases:
        state.mark_phase_done(p, "test-plan")
    with pytest.raises(ConfigError, match="Valid phases: alpha, beta, gamma"):
        list(DeployRunner(fake_cfg, tmp_path, from_phase="bogus").run())
    assert fake_profile.ran == []
    assert all(state.is_phase_done(p, "test-plan") for p in fake_profile.phases)


def test_runner_accepts_valid_from_phase(fake_profile, fake_cfg, tmp_path):
    list(DeployRunner(fake_cfg, tmp_path, from_phase="gamma").run())
    assert fake_profile.ran == ["gamma"]


# --- RodeoProfile.run_phase(phase) ------------------------------------------

class _StubRunner:
    def __init__(self):
        self.cfg = {"vms": {}}
        self._last_rc = None
        self.finalised = False

    def stream_finalise(self):
        self.finalised = True
        self._last_rc = 0
        yield from ()


@pytest.mark.parametrize("profile_name", ["suse-virt", "suse-telco", "lab-in-a-box"])
def test_run_phase_rejects_unknown_phase(profile_name):
    profile = get_profile(profile_name)
    runner = _StubRunner()
    with pytest.raises(ConfigError, match="Unknown phase 'bogus'") as exc:
        list(profile.run_phase("bogus", runner, Path("/nonexistent")))
    assert ", ".join(profile.phases) in str(exc.value)
    assert runner._last_rc is None


def test_run_phase_accepts_valid_stream_phase():
    runner = _StubRunner()
    list(get_profile("suse-virt").run_phase("finalise", runner, Path("/nonexistent")))
    assert runner.finalised and runner._last_rc == 0
