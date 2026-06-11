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


def test_sleep_reports_cancellation():
    p = ClusterPhase(
        {"network": {"vip": "10.0.0.10"}}, stop=threading.Event()
    )
    assert p._sleep(0) is False
    p._stop.set()
    assert p._sleep(10) is True  # returns immediately, no 10 s wait
    assert p.error == "cancelled"
