"""RancherPhase subprocess safety and config wiring."""
from __future__ import annotations

import subprocess

import pytest

from rodeo.engine import rancher as rancher_mod
from rodeo.engine.rancher import RancherPhase

from .conftest import drain


@pytest.fixture
def cfg():
    return {
        "network": {
            "vip": "10.0.0.10",
            "rancher_ip": "10.0.0.9",
            "gateway": "10.0.0.1",
            "dns_domain": "lab.example",
        },
        "credentials": {"lab_admin_password": "Secret123"},
        "vms": {
            "harvester1": {}, "harvester2": {}, "harvester3": {}, "rancher": {},
        },
    }


def test_ssh_script_timeout_returns_failed_result(cfg, monkeypatch):
    """Regression for F2: a hung SSH session must not raise."""
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0], timeout=k.get("timeout", 0))

    monkeypatch.setattr(rancher_mod.subprocess, "run", _raise)
    r = RancherPhase(cfg)._ssh_script("echo hi", timeout=5)
    assert r.returncode == 124
    assert "timed out" in r.stderr


def test_run_missing_binary_returns_failed_result(cfg, monkeypatch):
    def _raise(*a, **k):
        raise FileNotFoundError("no such binary")

    monkeypatch.setattr(rancher_mod.subprocess, "run", _raise)
    r = RancherPhase(cfg)._run(["kubectl", "version"], timeout=5)
    assert r.returncode == 127


def test_install_k3s_timeout_fails_phase_cleanly(cfg, monkeypatch):
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0], timeout=k.get("timeout", 0))

    monkeypatch.setattr(rancher_mod.subprocess, "run", _raise)
    phase = RancherPhase(cfg)
    _, ok = drain(phase._install_k3s())
    assert ok is False
    assert phase.error == "K3s install failed"


def test_config_wiring(cfg):
    phase = RancherPhase(cfg)
    assert phase.gateway == "10.0.0.1"
    assert phase.dns_domain == "lab.example"
    assert phase.harvester_nodes == ["harvester1", "harvester2", "harvester3"]
    assert phase.admin_password == "Secret123"


def test_harvester_nodes_fallback_when_vms_missing(cfg):
    cfg.pop("vms")
    phase = RancherPhase(cfg)
    assert phase.harvester_nodes == ["harvester1", "harvester2", "harvester3"]


def test_import_fails_when_cluster_id_never_assigned(cfg, monkeypatch):
    """Provisioning API: if Rancher never assigns a cluster ID the import must fail."""
    phase = RancherPhase(cfg)
    phase._api_token = "token"

    # cluster apply succeeds; all status polls return empty (ID never assigned)
    def fake_ssh(self, script, timeout=60):
        if "kubectl apply" in script:
            return subprocess.CompletedProcess([], 0, stdout="configured", stderr="")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(RancherPhase, "_ssh_script", fake_ssh)
    monkeypatch.setattr(rancher_mod.time, "sleep", lambda _: None)

    # make the 120 s poll window expire after one iteration
    _calls = {"n": 0}
    def fake_monotonic():
        _calls["n"] += 1
        return 0.0 if _calls["n"] == 1 else 999.0

    monkeypatch.setattr(rancher_mod.time, "monotonic", fake_monotonic)

    _, ok = drain(phase._import_harvester())
    assert ok is False
    assert "Cluster ID not assigned" in phase.error


def test_wait_ssh_cancellable(cfg, monkeypatch):
    def _fail(*a, **k):
        return subprocess.CompletedProcess(a, 255, stdout="", stderr="refused")

    monkeypatch.setattr(rancher_mod.subprocess, "run", _fail)
    phase = RancherPhase(cfg)
    phase._stop.set()
    _, ok = drain(phase._wait_ssh())
    assert ok is False
    assert phase.error == "cancelled"
