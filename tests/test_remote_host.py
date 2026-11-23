"""``deployment_target: remote``: deploy to a KVM host over SSH (rodeo/providers/remote_host.py)."""
from __future__ import annotations

import pytest

from rodeo.config import ConfigError
from rodeo.providers import remote_host
from rodeo.providers.remote_up import remote_up_script


def _cfg(**block):
    return {"name": "ride", "deployment_target": "remote", "remote_host": dict(block)}


def test_validate_needs_a_host():
    with pytest.raises(ConfigError, match="requires a remote_host: block"):
        remote_host.validate({"deployment_target": "remote"})
    with pytest.raises(ConfigError, match="needs a host:"):
        remote_host.validate(_cfg(ssh_user="root"))


def test_validate_rejects_a_key_that_is_not_there(tmp_path):
    with pytest.raises(ConfigError, match="no such file"):
        remote_host.validate(_cfg(host="kvm-01", identity_file=str(tmp_path / "nope")))
    key = tmp_path / "id"
    key.write_text("KEY\n")
    remote_host.validate(_cfg(host="kvm-01", identity_file=str(key)))


def test_host_from_cfg_is_the_operators_host():
    host = remote_host.host_from_cfg(_cfg(host=" kvm-01.lab ", ssh_user="ops"))
    assert host.ssh == "ops@kvm-01.lab" and host.public_ip == "kvm-01.lab"
    assert host.provider_id is None, "nothing is provisioned for a host the operator owns"
    assert remote_host.host_from_cfg(_cfg(host="kvm-01")).ssh == "root@kvm-01"


def test_deploys_to_remote_reads_the_target():
    assert remote_host.deploys_to_remote({"deployment_target": "Remote"})
    assert not remote_host.deploys_to_remote({"deployment_target": "baremetal"})
    assert not remote_host.deploys_to_remote({})


def test_execute_remote_up_runs_baremetal_on_the_host(monkeypatch, tmp_path):
    key = tmp_path / "id"
    key.write_text("KEY\n")
    seen = {}

    def fake_run(cfg, host, **kw):
        seen["host"], seen["kw"] = host, kw

    monkeypatch.setattr(remote_host, "run_remote_up", fake_run)
    cfg = _cfg(host="kvm-01", ssh_user="ops", identity_file=str(key), lab_dir="/srv/lab")
    host = remote_host.execute_remote_up(cfg, profile="harvester", plan_dir=tmp_path)
    assert host.public_ip == "kvm-01"
    # The host is a plain KVM host: it must not be told to ship the lab on to itself.
    assert seen["kw"]["target"] == "baremetal"
    assert seen["kw"]["ssh_user"] == "ops" and seen["kw"]["identity_file"] == str(key)
    assert seen["kw"]["lab_dir"] == "/srv/lab" and seen["kw"]["plan_dir"] == tmp_path
    assert seen["kw"]["profile"] == "harvester"


def test_execute_remote_up_validates_before_it_connects(monkeypatch):
    monkeypatch.setattr(remote_host, "run_remote_up",
                        lambda *a, **k: pytest.fail("connected without a host"))
    with pytest.raises(ConfigError):
        remote_host.execute_remote_up({"deployment_target": "remote"})


def test_identity_file_expands_a_home_path(monkeypatch):
    monkeypatch.setenv("HOME", "/home/someone")
    assert remote_host.identity_file(_cfg(host="h", identity_file="~/.ssh/id")) == "/home/someone/.ssh/id"
    assert remote_host.identity_file(_cfg(host="h")) is None


def test_remote_script_logs_under_its_own_name_and_keeps_aws_default():
    script = remote_up_script(lab_dir="/root/lab", profile="t", target="baremetal", log_name="remote")
    assert "--target baremetal" in script
    # Its own log, pid and exit files: an aws deploy and a KVM-host deploy from the same
    # control plane must not claim each other's pid file. $HOME stays unquoted so the
    # remote shell expands it.
    assert 'log="$HOME/.rodeo/logs/remote-up.log"' in script
    assert 'pidf="$HOME/.rodeo/logs/remote-up.pid"' in script
    assert 'exitf="$HOME/.rodeo/logs/remote-up.exit"' in script
    aws = remote_up_script(lab_dir="/root/lab", profile="t")
    assert "--target aws" in aws and 'log="$HOME/.rodeo/logs/aws-up.log"' in aws


def test_a_detached_remote_run_records_itself_under_its_own_name():
    script = remote_up_script(lab_dir="/root/lab", profile="t", target="baremetal",
                              log_name="remote", detach=True)
    assert 'pidf="$HOME/.rodeo/logs/remote-up.pid"' in script
    assert 'exitf="$HOME/.rodeo/logs/remote-up.exit"' in script
    assert '>>"$HOME/.rodeo/logs/remote-up.log"' in script


def test_an_odd_target_name_cannot_escape_the_log_file_name():
    script = remote_up_script(lab_dir="/root/lab", profile="t", target="a b;rm -rf /")
    assert 'log="$HOME/.rodeo/logs/a-b-rm--rf---up.log"' in script
    assert "rm -rf /-up.log" not in script
    # The target itself still reaches rodeo quoted, as one argument.
    assert "--target 'a b;rm -rf /'" in script


def test_a_plan_targeting_remote_without_a_host_does_not_validate():
    from rodeo.config import validate_config

    plan = {"name": "x", "type": "rancher", "deployment_target": "remote",
            "credentials": {"admin": "secret123"}}
    with pytest.raises(ConfigError, match="remote_host"):
        validate_config(plan)


def test_remote_is_a_known_target_shaped_like_baremetal():
    from rodeo.host_context import apply_host_context, is_known_target, known_targets

    assert is_known_target("remote") and "remote" in known_targets()
    shaped, _notes = apply_host_context({"name": "x", "deployment_target": "remote"}, host_facts={})
    baremetal, _ = apply_host_context({"name": "x", "deployment_target": "baremetal"}, host_facts={})
    shaped.pop("deployment_target"), baremetal.pop("deployment_target")
    assert shaped == baremetal


def test_profile_install_accepts_the_remote_target():
    from rodeo.profile_install import _TARGETS

    assert "remote" in _TARGETS
