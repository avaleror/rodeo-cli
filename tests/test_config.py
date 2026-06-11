"""Config loading, secret resolution, and validation."""
from __future__ import annotations

import pytest
import yaml

from rodeo import config


def _write_plan(tmp_path, data):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text(yaml.dump(data))
    return plan


def _write_secrets(tmp_path, monkeypatch, data):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text(yaml.dump(data))
    monkeypatch.setattr(config, "_SECRETS_PATH", secrets)
    return secrets


def test_defaults_when_no_plan(tmp_path):
    cfg = config.load_config(tmp_path / "missing.yaml")
    assert cfg["type"] == "suse-virt"
    assert cfg["deployment_target"] == "baremetal"
    assert cfg["network"]["vip"] == "192.168.122.10"
    # suse-virt profile defaults merged in
    assert cfg["vms"]["harvester1"]["ip"] == "192.168.122.11"
    assert cfg["resources"]["harvester"]["memory_mib"] == 16384


def test_plan_overrides_defaults(tmp_path):
    plan = _write_plan(tmp_path, {"network": {"vip": "10.1.1.1"}})
    cfg = config.load_config(plan)
    assert cfg["network"]["vip"] == "10.1.1.1"
    assert cfg["network"]["rancher_ip"] == "192.168.122.9"  # untouched default


def test_secret_resolution(tmp_path, monkeypatch):
    _write_secrets(tmp_path, monkeypatch, {"harvester_os_password": "FromSecrets1"})
    plan = _write_plan(
        tmp_path, {"credentials": {"harvester_os_password": "??harvester_os_password"}}
    )
    cfg = config.load_config(plan)
    assert cfg["credentials"]["harvester_os_password"] == "FromSecrets1"


def test_env_resolver(tmp_path, monkeypatch):
    monkeypatch.setenv("RODEO_TEST_PW", "FromEnv12345")
    plan = _write_plan(
        tmp_path, {"credentials": {"harvester_os_password": "??env:RODEO_TEST_PW"}}
    )
    cfg = config.load_config(plan)
    assert cfg["credentials"]["harvester_os_password"] == "FromEnv12345"


def test_env_resolver_missing_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("RODEO_MISSING_PW", raising=False)
    plan = _write_plan(
        tmp_path, {"credentials": {"harvester_os_password": "??env:RODEO_MISSING_PW"}}
    )
    cfg = config.load_config(plan)
    assert cfg["credentials"]["harvester_os_password"] == "??env:RODEO_MISSING_PW"
    with pytest.raises(ValueError, match="Secrets not resolved"):
        config.validate_config(cfg)


def test_file_resolver(tmp_path):
    pw_file = tmp_path / "pw.txt"
    pw_file.write_text("FromFile12345\n")
    plan = _write_plan(
        tmp_path, {"credentials": {"harvester_os_password": f"??file:{pw_file}"}}
    )
    cfg = config.load_config(plan)
    assert cfg["credentials"]["harvester_os_password"] == "FromFile12345"


def test_file_resolver_missing_fails_closed(tmp_path):
    plan = _write_plan(
        tmp_path, {"credentials": {"harvester_os_password": "??file:/nonexistent/pw"}}
    )
    cfg = config.load_config(plan)
    with pytest.raises(ValueError, match="Secrets not resolved"):
        config.validate_config(cfg)


def test_cmd_resolver(tmp_path):
    plan = _write_plan(
        tmp_path, {"credentials": {"harvester_os_password": "??cmd:echo FromCmd12345"}}
    )
    cfg = config.load_config(plan)
    assert cfg["credentials"]["harvester_os_password"] == "FromCmd12345"


def test_cmd_resolver_failure_fails_closed(tmp_path):
    plan = _write_plan(
        tmp_path, {"credentials": {"harvester_os_password": "??cmd:false"}}
    )
    cfg = config.load_config(plan)
    with pytest.raises(ValueError, match="Secrets not resolved"):
        config.validate_config(cfg)


def test_unresolved_secret_kept_and_rejected(tmp_path, monkeypatch):
    _write_secrets(tmp_path, monkeypatch, {})
    plan = _write_plan(
        tmp_path, {"credentials": {"harvester_os_password": "??harvester_os_password"}}
    )
    cfg = config.load_config(plan)
    assert cfg["credentials"]["harvester_os_password"] == "??harvester_os_password"
    with pytest.raises(ValueError, match="Secrets not resolved"):
        config.validate_config(cfg)


@pytest.mark.parametrize("bad", [None, "", "   ", "CHANGE_ME"])
def test_empty_credentials_rejected(bad):
    cfg = {"credentials": {"harvester_os_password": bad, "lab_admin_password": "x1234"}}
    with pytest.raises(ValueError, match="Credentials are empty"):
        config.validate_config(cfg)


def test_bad_deployment_target_rejected():
    cfg = {
        "credentials": {"harvester_os_password": "Secret123"},
        "deployment_target": "cloud",
    }
    with pytest.raises(ValueError, match="deployment_target"):
        config.validate_config(cfg)


def test_valid_config_passes():
    cfg = {
        "credentials": {"harvester_os_password": "Secret123"},
        "deployment_target": "instruqt",
    }
    config.validate_config(cfg)  # must not raise


def test_find_ansible_root_prefers_explicit(tmp_path):
    explicit = tmp_path / "custom"
    (explicit / "ansible").mkdir(parents=True)
    (explicit / "ansible" / "playbook.yml").write_text("---\n")
    cfg = {"ansible": {"path": str(explicit)}}
    assert config.find_ansible_root(cfg) == explicit


def test_find_ansible_root_falls_back_to_bundled():
    cfg = {"ansible": {"path": None}}
    root = config.find_ansible_root(cfg)
    assert root is not None
    assert (root / "ansible" / "playbook.yml").exists()
