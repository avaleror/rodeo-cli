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


def test_import_fails_when_cluster_never_active(cfg, monkeypatch):
    """Regression for the silent-success import: a cluster stuck outside
    'active' must fail the phase, not log a warning and return True."""
    phase = RancherPhase(cfg)
    phase._api_token = "token"

    responses = {
        ("POST", "/v3/clusters"): {"id": "c-m-test"},
        ("GET", "/v3/clusterregistrationtokens?clusterId=c-m-test"): {
            "data": [{"manifestUrl": "https://rancher/manifest.yaml"}]
        },
    }
    monkeypatch.setattr(
        RancherPhase, "_http",
        lambda self, method, path, data=None, token="": responses[(method, path)],
    )
    # kubectl calls (CoreDNS probe + manifest apply) succeed
    monkeypatch.setattr(
        rancher_mod.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="", stderr=""),
    )
    # kubeconfig copy is irrelevant here
    monkeypatch.setattr(
        RancherPhase, "_wait_cluster_active",
        lambda self: iter(()),  # empty generator -> return value None -> falsy
    )

    _, ok = drain(phase._import_harvester())
    assert ok is False
    assert "did not reach Active" in phase.error


def test_wait_ssh_cancellable(cfg, monkeypatch):
    def _fail(*a, **k):
        return subprocess.CompletedProcess(a, 255, stdout="", stderr="refused")

    monkeypatch.setattr(rancher_mod.subprocess, "run", _fail)
    phase = RancherPhase(cfg)
    phase._stop.set()
    _, ok = drain(phase._wait_ssh())
    assert ok is False
    assert phase.error == "cancelled"
