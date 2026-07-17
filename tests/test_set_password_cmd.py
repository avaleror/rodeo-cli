"""rodeo set-password — rotate Harvester/Rancher admin passwords post-deploy."""
from __future__ import annotations

import yaml
from click.testing import CliRunner

from rodeo import secretgen
from rodeo.commands import set_password_cmd as spc_mod
from rodeo.engine import rancher as rancher_mod


def _write_plan(tmp_path, vms):
    plan = {
        "type": "harvester",
        "name": "test-plan",
        "network": {"vip": "10.0.0.10", "rancher_ip": "10.0.0.9"},
        "vms": vms,
    }
    path = tmp_path / "rodeo-plan.yaml"
    path.write_text(yaml.safe_dump(plan))
    return path


class FakePhase:
    """Stands in for RancherPhase — records which sub-steps were invoked."""

    calls: list[str] = []

    def __init__(self, cfg):
        self.cfg = cfg
        self.tls_source = "rancher"
        self.error = ""
        self.harvester_password_error = ""

    def _configure_api(self):
        FakePhase.calls.append("rancher")
        return iter(())

    def _set_harvester_password(self):
        FakePhase.calls.append("harvester")
        return iter(())


def test_errors_without_existing_secrets_file(tmp_path, monkeypatch):
    monkeypatch.setattr(spc_mod, "is_root", lambda: True)
    plan = _write_plan(tmp_path, {"harvester1": {}, "harvester2": {}, "harvester3": {}, "rancher": {}})
    result = CliRunner().invoke(spc_mod.set_password_cmd, ["--config", str(plan), "--yes"])
    assert result.exit_code == 1
    assert "has this lab" in result.output.replace("\n", " ")


def test_rotates_both_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(spc_mod, "is_root", lambda: True)
    secretgen.ensure_secrets_file()  # HOME=tmp_path (conftest isolated_env)
    plan = _write_plan(tmp_path, {"harvester1": {}, "harvester2": {}, "harvester3": {}, "rancher": {}})

    FakePhase.calls = []
    monkeypatch.setattr(rancher_mod, "RancherPhase", FakePhase)

    result = CliRunner().invoke(spc_mod.set_password_cmd, ["--config", str(plan), "--yes"])
    assert result.exit_code == 0, result.output
    assert set(FakePhase.calls) == {"rancher", "harvester"}

    secrets_path = spc_mod.rodeo_secrets_path()
    text = secrets_path.read_text()
    assert "New password:" in result.output
    # Same new password landed on both keys.
    harvester_pw = [line for line in text.splitlines() if line.startswith("harvester_admin_password:")][0]
    rancher_pw = [line for line in text.splitlines() if line.startswith("rancher_admin_password:")][0]
    assert harvester_pw.split(":", 1)[1] == rancher_pw.split(":", 1)[1]


def test_target_harvester_only_touches_harvester(tmp_path, monkeypatch):
    monkeypatch.setattr(spc_mod, "is_root", lambda: True)
    secretgen.ensure_secrets_file()
    plan = _write_plan(tmp_path, {"harvester1": {}, "harvester2": {}, "harvester3": {}, "rancher": {}})

    FakePhase.calls = []
    monkeypatch.setattr(rancher_mod, "RancherPhase", FakePhase)

    secrets_path = spc_mod.rodeo_secrets_path()
    before = [line for line in secrets_path.read_text().splitlines() if line.startswith("rancher_admin_password:")][0]

    result = CliRunner().invoke(
        spc_mod.set_password_cmd, ["--config", str(plan), "--yes", "--target", "harvester"]
    )
    assert result.exit_code == 0, result.output
    assert FakePhase.calls == ["harvester"]

    after = [line for line in secrets_path.read_text().splitlines() if line.startswith("rancher_admin_password:")][0]
    assert before == after  # untouched


def test_reports_failure_and_keeps_new_password_in_secrets(tmp_path, monkeypatch):
    """If a side isn't reachable, the new password must still be left on disk
    (so a retry or a redeploy can pick it up) and the command must exit non-zero."""
    monkeypatch.setattr(spc_mod, "is_root", lambda: True)
    secretgen.ensure_secrets_file()
    plan = _write_plan(tmp_path, {"harvester1": {}, "harvester2": {}, "harvester3": {}, "rancher": {}})

    class FailingPhase(FakePhase):
        def _configure_api(self):
            FakePhase.calls.append("rancher")
            self.error = "Rancher did not respond"
            return iter(())

    FakePhase.calls = []
    monkeypatch.setattr(rancher_mod, "RancherPhase", FailingPhase)

    result = CliRunner().invoke(spc_mod.set_password_cmd, ["--config", str(plan), "--yes"])
    assert result.exit_code == 1
    flat = result.output.replace("\n", " ")
    assert "Rancher did not respond" in flat
    assert "is saved in" in flat

    secrets_path = spc_mod.rodeo_secrets_path()
    assert "harvester_admin_password:" in secrets_path.read_text()
