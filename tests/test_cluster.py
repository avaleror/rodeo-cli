"""ClusterPhase wait loops and parsing (no KVM required)."""
from __future__ import annotations

import io
import subprocess
import threading
import urllib.error

import pytest

from rodeo.engine import cluster as cluster_mod
from rodeo.engine.cluster import ClusterPhase

from .conftest import drain


@pytest.fixture
def phase():
    cfg = {
        "network": {"vip": "10.0.0.10"},
        "vms": {"vm1": {"ip": "10.0.0.1", "user": "root"}},
    }
    p = ClusterPhase(cfg)
    p.VIP_POLL = 0  # no real sleeping in tests
    return p


def test_wait_vip_succeeds_on_clean_response(phase, monkeypatch):
    monkeypatch.setattr(
        cluster_mod.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"ok")
    )
    _, ok = drain(phase._wait_vip())
    assert ok is True


def test_wait_vip_treats_http_error_as_up(phase, monkeypatch):
    """Regression for H3: a 401/503 means the VIP answered."""
    def _raise(*a, **k):
        raise urllib.error.HTTPError("https://10.0.0.10", 401, "unauthorized", {}, None)

    monkeypatch.setattr(cluster_mod.urllib.request, "urlopen", _raise)
    _, ok = drain(phase._wait_vip())
    assert ok is True


def test_wait_vip_cancellable(phase, monkeypatch):
    def _raise(*a, **k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(cluster_mod.urllib.request, "urlopen", _raise)
    phase._stop.set()
    _, ok = drain(phase._wait_vip())
    assert ok is False
    assert phase.error == "cancelled"


def test_count_ready_nodes_handles_scheduling_disabled(phase, monkeypatch):
    out = (
        "harvester1   Ready                      control-plane   1h  v1.32\n"
        "harvester2   Ready,SchedulingDisabled   control-plane   1h  v1.32\n"
        "harvester3   NotReady                   control-plane   1h  v1.32\n"
    )
    monkeypatch.setattr(
        cluster_mod.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""),
    )
    assert phase._count_ready_nodes() == 2


def test_count_ready_nodes_survives_missing_kubectl(phase, monkeypatch):
    def _raise(*a, **k):
        raise FileNotFoundError("kubectl")

    monkeypatch.setattr(cluster_mod.subprocess, "run", _raise)
    assert phase._count_ready_nodes() == 0


def test_apply_longhorn_settings_patches_managedchart(phase, monkeypatch):
    """Regression: patching settings.longhorn.io directly gets silently reverted
    within ~1-2 min by Fleet reconciling the ManagedChart's declared "0" back over
    it (verified live) — the ManagedChart itself must be patched instead, since
    that's Fleet's actual source of truth and propagates down reliably."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(cluster_mod.subprocess, "run", fake_run)
    events = list(phase._apply_longhorn_settings())
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[:2] == ["kubectl", "--kubeconfig"]
    assert "managedchart" in cmd
    assert "harvester" in cmd
    assert "fleet-local" in cmd
    assert '"storageReservedPercentageForDefaultDisk": "10"' in cmd[-1]
    assert any("set to 10%" in e.line for e in events)


def test_apply_longhorn_settings_survives_kubectl_failure(phase, monkeypatch):
    monkeypatch.setattr(
        cluster_mod.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess([], 1, stdout="", stderr="not found"),
    )
    events = list(phase._apply_longhorn_settings())
    assert any("could not set Longhorn reserved-%" in e.line for e in events)


def test_sleep_reports_cancellation():
    p = ClusterPhase(
        {"network": {"vip": "10.0.0.10"}}, stop=threading.Event()
    )
    assert p._sleep(0) is False
    p._stop.set()
    assert p._sleep(10) is True  # returns immediately, no 10 s wait
    assert p.error == "cancelled"
