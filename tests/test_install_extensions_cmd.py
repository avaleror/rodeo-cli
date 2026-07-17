"""rodeo install-extensions — reconcile Rancher UI extensions post-deploy."""
from __future__ import annotations

import yaml
from click.testing import CliRunner

from rodeo.commands import install_extensions_cmd as iec_mod
from rodeo.engine import rancher as rancher_mod


def _write_plan(tmp_path, vms, extra=None):
    plan = {
        "type": "harvester",
        "name": "test-plan",
        "network": {"vip": "10.0.0.10", "rancher_ip": "10.0.0.9"},
        "vms": vms,
        **(extra or {}),
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

    def _configure_api(self):
        FakePhase.calls.append("configure_api")
        return iter(())

    def _reconcile_ui_extensions(self):
        FakePhase.calls.append("reconcile_ui_extensions")
        return iter(())


def test_errors_without_rancher_component(tmp_path, monkeypatch):
    monkeypatch.setattr(iec_mod, "is_root", lambda: True)
    plan = _write_plan(tmp_path, {"harvester1": {}, "harvester2": {}, "harvester3": {}})
    result = CliRunner().invoke(iec_mod.install_extensions_cmd, ["--config", str(plan), "--yes"])
    assert result.exit_code == 1
    assert "no Rancher Prime component" in result.output.replace("\n", " ")


def test_errors_without_declared_extensions(tmp_path, monkeypatch):
    monkeypatch.setattr(iec_mod, "is_root", lambda: True)
    plan = _write_plan(tmp_path, {"harvester1": {}, "harvester2": {}, "harvester3": {}, "rancher": {}})
    result = CliRunner().invoke(iec_mod.install_extensions_cmd, ["--config", str(plan), "--yes"])
    assert result.exit_code == 1
    assert "No rancher_ui_extensions declared" in result.output.replace("\n", " ")


def test_reconciles_declared_extensions(tmp_path, monkeypatch):
    monkeypatch.setattr(iec_mod, "is_root", lambda: True)
    plan = _write_plan(
        tmp_path,
        {"harvester1": {}, "harvester2": {}, "harvester3": {}, "rancher": {}},
        extra={"rancher_ui_extensions": [{"name": "harvester", "version": "1.8.1"}]},
    )
    FakePhase.calls = []
    monkeypatch.setattr(rancher_mod, "RancherPhase", FakePhase)

    result = CliRunner().invoke(iec_mod.install_extensions_cmd, ["--config", str(plan), "--yes"])
    assert result.exit_code == 0, result.output
    assert FakePhase.calls == ["configure_api", "reconcile_ui_extensions"]
    assert "Extensions reconciled" in result.output


def test_stops_after_auth_failure_without_reconciling(tmp_path, monkeypatch):
    monkeypatch.setattr(iec_mod, "is_root", lambda: True)
    plan = _write_plan(
        tmp_path,
        {"harvester1": {}, "harvester2": {}, "harvester3": {}, "rancher": {}},
        extra={"rancher_ui_extensions": [{"name": "harvester", "version": "1.8.1"}]},
    )

    class FailingAuthPhase(FakePhase):
        def _configure_api(self):
            FakePhase.calls.append("configure_api")
            self.error = "Rancher login failed"
            return iter(())

    FakePhase.calls = []
    monkeypatch.setattr(rancher_mod, "RancherPhase", FailingAuthPhase)

    result = CliRunner().invoke(iec_mod.install_extensions_cmd, ["--config", str(plan), "--yes"])
    assert result.exit_code == 1
    assert "Rancher login failed" in result.output
    assert FakePhase.calls == ["configure_api"]  # never reached reconcile
