"""RancherPhase subprocess safety and config wiring."""
from __future__ import annotations

import subprocess
from pathlib import Path

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
        "credentials": {"harvester_admin_password": "Secret123", "rancher_admin_password": "Secret123"},
        "vms": {
            "harvester1": {}, "harvester2": {}, "harvester3": {}, "rancher": {},
        },
    }


def test_rancher_phase_has_no_raw_time_sleep():
    """Poll loops must use _sleep() so TUI quit / runner.stop cancels promptly."""
    source = Path(rancher_mod.__file__).read_text()
    assert "time.sleep" not in source


def test_leap_micro_urls_default_to_opensuse_not_stale_suse_com(cfg):
    """Regression: download.suse.com's SL-Micro path redirects to a marketing page,
    not the file (confirmed live) — defaults must point at opensuse.org instead."""
    phase = RancherPhase(cfg)
    assert phase.leap_micro_iso_url.startswith("https://download.opensuse.org/")
    assert phase.leap_micro_raw_url.startswith("https://download.opensuse.org/")
    assert "download.suse.com" not in phase.leap_micro_iso_url
    assert "download.suse.com" not in phase.leap_micro_raw_url


def test_leap_micro_urls_overridable_via_eib_config(cfg):
    cfg["eib"] = {
        "leap_micro_iso_url": "https://example.internal/custom.iso",
        "leap_micro_raw_url": "https://example.internal/custom.raw.xz",
    }
    phase = RancherPhase(cfg)
    assert phase.leap_micro_iso_url == "https://example.internal/custom.iso"
    assert phase.leap_micro_raw_url == "https://example.internal/custom.raw.xz"


def test_sleep_reports_cancelled(cfg):
    phase = RancherPhase(cfg)
    phase._stop.set()
    assert phase._sleep(1) is True
    assert phase.error == "cancelled"


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


def test_auto_import_defaults_off(cfg):
    # The workshop model is manual import; nothing should auto-import unless the
    # plan opts in explicitly.
    assert RancherPhase(cfg).harvester_auto_import is False


def test_auto_import_opt_in(cfg):
    cfg["harvester_auto_import"] = True
    assert RancherPhase(cfg).harvester_auto_import is True


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

    # make the 120 s poll window expire after one iteration
    _calls = {"n": 0}
    def fake_monotonic():
        _calls["n"] += 1
        return 0.0 if _calls["n"] == 1 else 999.0

    monkeypatch.setattr(rancher_mod.time, "monotonic", fake_monotonic)

    _, ok = drain(phase._import_harvester())
    assert ok is False
    assert "Cluster ID not assigned" in phase.error


def test_import_fails_when_cluster_never_active(cfg, monkeypatch, tmp_path):
    """Regression: cluster stuck outside 'active' must fail _import_harvester."""
    phase = RancherPhase(cfg)
    phase._api_token = "token"

    def fake_ssh(self, script, timeout=60):
        if "kubectl apply" in script:
            return subprocess.CompletedProcess([], 0, stdout="configured", stderr="")
        if ".status.clusterName" in script:
            return subprocess.CompletedProcess([], 0, stdout="c-m-test123", stderr="")
        if ".status.manifestUrl" in script:
            return subprocess.CompletedProcess([], 0, stdout="https://rancher/manifest.yaml", stderr="")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(RancherPhase, "_ssh_script", fake_ssh)
    monkeypatch.setattr(
        rancher_mod.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            list(a[0]) if a else [], 0, stdout="", stderr=""
        ),
    )

    kube = tmp_path / "harvester-kubeconfig"
    kube.write_text("dummy")
    monkeypatch.setattr(rancher_mod, "harvester_kubeconfig_path", lambda: kube)

    # _wait_cluster_active returns an empty iterator → None return value → falsy
    monkeypatch.setattr(RancherPhase, "_wait_cluster_active", lambda self: iter(()))

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


def test_install_rancher_uses_values_file_not_set_bootstrap(cfg, monkeypatch):
    """bootstrapPassword must not appear on helm argv (quoting + process-list risk)."""
    cfg["credentials"]["rancher_admin_password"] = 'p@ss"word$xyz'
    scripts: list[str] = []

    def fake_ssh(self, script, timeout=120):
        scripts.append(script)
        return subprocess.CompletedProcess([], 0, stdout="deployed", stderr="")

    monkeypatch.setattr(RancherPhase, "_ssh_script", fake_ssh)
    phase = RancherPhase(cfg)
    _, ok = drain(phase._install_rancher())
    assert ok is True
    assert len(scripts) == 1
    script = scripts[0]
    assert "--set bootstrapPassword" not in script
    assert "--set hostname=" not in script
    assert "-f /root/rancher-helm-values.yaml" in script
    assert "chmod 600 /root/rancher-helm-values.yaml" in script
    assert "rm -f /root/rancher-helm-values.yaml" in script
    assert "bootstrapPassword:" in script
    assert 'p@ss"word$xyz' in script


def test_install_rancher_letsencrypt_in_values_file(cfg, monkeypatch):
    cfg["rancher_tls"] = {"source": "letsEncrypt", "email": "ops@example.com"}
    scripts: list[str] = []

    def fake_ssh(self, script, timeout=120):
        scripts.append(script)
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(RancherPhase, "_ssh_script", fake_ssh)
    phase = RancherPhase(cfg)
    assert phase.tls_source == "letsEncrypt"
    _, ok = drain(phase._install_rancher())
    assert ok is True
    script = scripts[0]
    assert "--set letsEncrypt" not in script
    assert "--set ingress.tls" not in script
    assert "letsEncrypt:" in script
    assert "ops@example.com" in script
