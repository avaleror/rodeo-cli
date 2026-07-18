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


def test_set_harvester_password_runs_regardless_of_auto_import(cfg, monkeypatch):
    """Harvester's own dashboard password must be set even when auto-import is off
    (the workshop default) — it's independent of whether the cluster gets imported
    into Rancher. Previously this step was skipped entirely in that case."""
    cfg["harvester_auto_import"] = False
    phase = RancherPhase(cfg)
    phase.standalone = False
    calls: list[str] = []
    monkeypatch.setattr(RancherPhase, "_set_harvester_password", lambda self: calls.append("called") or iter(()))
    monkeypatch.setattr(RancherPhase, "_eject_cdroms", lambda self: iter(()))
    drain(phase.stream_import())
    assert calls == ["called"]


def test_set_harvester_password_uses_persisted_file_as_fallback_credential(cfg, monkeypatch, tmp_path):
    """A redeploy after secrets.yaml is regenerated must still authenticate — using
    the last password this tool set (persisted on the host) — and roll the live
    password forward to the newly configured one."""
    pw_file = tmp_path / "harvester-password"
    pw_file.write_text("OldPassword1")
    monkeypatch.setattr(RancherPhase, "HARVESTER_PW_FILE", pw_file)

    phase = RancherPhase(cfg)  # cfg's harvester_admin_password is "Secret123"
    state = {"live_password": "OldPassword1"}
    attempted: list[str] = []

    def fake_harvester_login(self, password):
        attempted.append(password)
        if password == state["live_password"]:
            return "tok", ""
        return "", "invalid credentials"

    class FakeResp:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, context=None, timeout=None):
        import json as _json
        body = _json.loads(req.data)
        assert body["currentPassword"] == "OldPassword1"
        state["live_password"] = body["newPassword"]
        return FakeResp()

    monkeypatch.setattr(RancherPhase, "_harvester_login", fake_harvester_login)
    monkeypatch.setattr(rancher_mod.urllib.request, "urlopen", fake_urlopen)

    events, _ = drain(phase._set_harvester_password())
    assert phase.harvester_password_error == ""
    assert "Secret123" in attempted  # tried the configured password first
    assert "OldPassword1" in attempted  # fell back to the persisted one
    assert state["live_password"] == "Secret123"
    assert pw_file.read_text() == "Secret123"


def test_configure_api_uses_persisted_file_as_fallback_credential(cfg, monkeypatch, tmp_path):
    """Same self-heal as Harvester, on the Rancher side: a stale live password
    (from before secrets.yaml was regenerated) must still let a redeploy in."""
    pw_file = tmp_path / "rancher-password"
    pw_file.write_text("OldPassword1")
    monkeypatch.setattr(RancherPhase, "RANCHER_PW_FILE", pw_file)

    phase = RancherPhase(cfg)  # cfg's rancher_admin_password is "Secret123"
    monkeypatch.setattr(RancherPhase, "_clear_must_change_password", lambda self: None)
    monkeypatch.setattr(RancherPhase, "_clear_first_login", lambda self: None)
    monkeypatch.setattr(RancherPhase, "_get_bootstrap_password", lambda self: "admin")
    monkeypatch.setattr(RancherPhase, "_sync_cacerts", lambda self: iter(()))

    state = {"live_password": "OldPassword1"}
    attempted: list[str] = []

    def fake_http(self, method, path, data=None, token=""):
        if path == "/v3-public/localProviders/local?action=login":
            attempted.append(data["password"])
            if data["password"] != state["live_password"]:
                raise RuntimeError("invalid credentials")
            return {"token": "tok"}
        if path == "/v3/users?me=true":
            return {"data": [{"id": "user-abc"}]}
        if path.endswith("action=setpassword"):
            state["live_password"] = data["newPassword"]
            return {}
        return {}

    monkeypatch.setattr(RancherPhase, "_http", fake_http)

    events, ok = drain(phase._configure_api())
    assert ok is True
    assert phase.error == ""
    assert "Secret123" in attempted
    assert "OldPassword1" in attempted
    assert state["live_password"] == "Secret123"
    assert pw_file.read_text() == "Secret123"


def test_clear_first_login_patches_setting_false(cfg, monkeypatch):
    phase = RancherPhase(cfg)
    scripts: list[str] = []

    def fake_ssh(self, script, timeout=15):
        scripts.append(script)
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(RancherPhase, "_ssh_script", fake_ssh)
    phase._clear_first_login()

    assert len(scripts) == 1
    assert "settings.management.cattle.io first-login" in scripts[0]
    assert '"value":"false"' in scripts[0]


def test_configure_api_clears_first_login_even_when_already_set(cfg, monkeypatch):
    """Regression: when secrets.yaml's password already matches bootstrapPassword,
    login succeeds on the first try and setpassword (which would otherwise clear
    first-login as a side effect) is never called — first-login must still be
    cleared explicitly, or the dashboard gets stuck on the "create your password"
    wizard despite the credentials already being correct."""
    phase = RancherPhase(cfg)  # cfg's rancher_admin_password is "Secret123"
    monkeypatch.setattr(RancherPhase, "_clear_must_change_password", lambda self: None)
    monkeypatch.setattr(RancherPhase, "_get_bootstrap_password", lambda self: "Secret123")
    monkeypatch.setattr(RancherPhase, "_sync_cacerts", lambda self: iter(()))

    cleared = {"n": 0}
    monkeypatch.setattr(RancherPhase, "_clear_first_login", lambda self: cleared.__setitem__("n", cleared["n"] + 1))

    setpassword_calls = {"n": 0}

    def fake_http(self, method, path, data=None, token=""):
        if path == "/v3-public/localProviders/local?action=login":
            return {"token": "tok"}
        if path == "/v3/users?me=true":
            return {"data": [{"id": "user-abc"}]}
        if path.endswith("action=setpassword"):
            setpassword_calls["n"] += 1
            return {}
        return {}

    monkeypatch.setattr(RancherPhase, "_http", fake_http)

    events, ok = drain(phase._configure_api())
    assert ok is True
    assert setpassword_calls["n"] == 0  # confirms this is the "already set" branch
    assert cleared["n"] == 1  # first-login must still be cleared
