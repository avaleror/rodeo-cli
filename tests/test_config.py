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
    secrets = tmp_path / ".rodeo" / "secrets.yaml"
    secrets.parent.mkdir(parents=True, exist_ok=True)
    secrets.write_text(yaml.dump(data))
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
    cfg = {"credentials": {"harvester_os_password": bad, "rancher_admin_password": "x1234"}}
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


def test_vip_collision_with_node_ip_rejected():
    cfg = {
        "credentials": {"harvester_os_password": "Secret123"},
        "network": {"vip": "192.168.122.11"},
        "vms": {"harvester1": {"ip": "192.168.122.11", "user": "rancher"}},
    }
    with pytest.raises(ValueError, match="collides"):
        config.validate_config(cfg)


def test_rancher_ip_mismatch_rejected():
    cfg = {
        "credentials": {"harvester_os_password": "Secret123"},
        "network": {"vip": "192.168.122.10", "rancher_ip": "192.168.122.9"},
        "vms": {"rancher": {"ip": "192.168.122.99", "user": "root"}},
    }
    with pytest.raises(ValueError, match="rancher_ip"):
        config.validate_config(cfg)


def test_letsencrypt_unresolved_email_rejected():
    """Regression: an unresolved ??key in rancher_tls.email isn't in the credentials{}
    loop's fail-closed check, so it used to sail through as a literal string all the
    way to Let's Encrypt's ACME server (which rejects it: "contact email contains a
    question mark") — only surfacing after the rancher phase burned 10+ minutes."""
    cfg = {
        "credentials": {"harvester_os_password": "Secret123"},
        "rancher_tls": {"source": "letsEncrypt", "email": "??rancher_letsencrypt_email"},
    }
    with pytest.raises(ValueError, match="real address"):
        config.validate_config(cfg)


@pytest.mark.parametrize("bad_email", ["", "admin@example.com", "someone@sub.example.com"])
def test_letsencrypt_placeholder_email_rejected(bad_email):
    cfg = {
        "credentials": {"harvester_os_password": "Secret123"},
        "rancher_tls": {"source": "letsEncrypt", "email": bad_email},
    }
    with pytest.raises(ValueError, match="real address"):
        config.validate_config(cfg)


def test_letsencrypt_real_email_accepted():
    cfg = {
        "credentials": {"harvester_os_password": "Secret123"},
        "rancher_tls": {"source": "letsEncrypt", "email": "andres@suse.com"},
    }
    config.validate_config(cfg)  # must not raise


def test_default_config_is_self_consistent():
    """The shipped defaults must pass their own network validation."""
    cfg = config.load_config("/nonexistent/plan.yaml")
    cfg["credentials"] = {"harvester_os_password": "Secret123"}
    config.validate_config(cfg)  # must not raise


def test_param_override_dotted_path_with_type_coercion(tmp_path):
    plan = _write_plan(tmp_path, {})
    cfg = config.load_config(
        plan,
        params=("resources.harvester.memory_mib=20480", "deployment_target=instruqt"),
    )
    assert cfg["resources"]["harvester"]["memory_mib"] == 20480  # int, not str
    assert cfg["deployment_target"] == "instruqt"


def test_param_invalid_format_rejected(tmp_path):
    plan = _write_plan(tmp_path, {})
    with pytest.raises(config.ConfigError, match="KEY=VALUE"):
        config.load_config(plan, params=("justakey",))


def test_paramfile_deep_merges_like_tfvars(tmp_path):
    plan = _write_plan(tmp_path, {"network": {"vip": "10.1.1.1"}})
    pf = tmp_path / "params.yaml"
    pf.write_text(yaml.dump({"network": {"vip": "10.2.2.2"}, "name": "from-paramfile"}))
    cfg = config.load_config(plan, paramfile=pf)
    assert cfg["network"]["vip"] == "10.2.2.2"   # paramfile beats plan
    assert cfg["name"] == "from-paramfile"
    assert cfg["network"]["rancher_ip"] == "192.168.122.9"  # merge, not replace


def test_param_beats_paramfile(tmp_path):
    plan = _write_plan(tmp_path, {})
    pf = tmp_path / "params.yaml"
    pf.write_text(yaml.dump({"network": {"vip": "10.2.2.2"}}))
    cfg = config.load_config(plan, paramfile=pf, params=("network.vip=10.3.3.3",))
    assert cfg["network"]["vip"] == "10.3.3.3"


def test_jinja_plan_with_parameters_block(tmp_path):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text(
        "parameters:\n"
        "  memory: 4096\n"
        "  domain: lab.example\n"
        "\n"
        "resources:\n"
        "  harvester:\n"
        "    memory_mib: {{ memory }}\n"
        "network:\n"
        "  dns_domain: \"{{ domain }}\"\n"
    )
    cfg = config.load_config(plan)
    assert cfg["resources"]["harvester"]["memory_mib"] == 4096
    assert cfg["network"]["dns_domain"] == "lab.example"
    assert "parameters" not in cfg


def test_jinja_parameter_overridden_by_cli(tmp_path):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text(
        "parameters:\n  memory: 4096\n\n"
        "resources:\n  harvester:\n    memory_mib: {{ memory }}\n"
    )
    cfg = config.load_config(plan, params=("memory=8192",))
    assert cfg["resources"]["harvester"]["memory_mib"] == 8192


def test_jinja_undefined_parameter_is_clear_error(tmp_path):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("network:\n  vip: \"{{ nope }}\"\n")
    with pytest.raises(config.ConfigError, match="undefined template parameter"):
        config.load_config(plan)


def test_malformed_yaml_is_clean_error(tmp_path):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("network:\n\tvip: broken-by-tab\n")
    with pytest.raises(config.ConfigError, match="invalid YAML"):
        config.load_config(plan)


def test_resource_sanity_rejected():
    cfg = {
        "credentials": {"harvester_os_password": "Secret123"},
        "resources": {"harvester": {"memory_mib": -4, "vcpu": 8, "disk_gb": 270}},
    }
    with pytest.raises(config.ConfigError, match="positive integer"):
        config.validate_config(cfg)


def test_bad_libvirt_disk_cache_rejected():
    cfg = {
        "credentials": {"harvester_os_password": "Secret123"},
        "libvirt": {"disk_cache": "turbo"},
    }
    with pytest.raises(config.ConfigError, match="disk_cache"):
        config.validate_config(cfg)


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
