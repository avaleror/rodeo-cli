"""Dead-man switch on every cloud host rodeo launches (rodeo/providers/deadman.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import yaml

from rodeo.config import ConfigError
from rodeo.providers import deadman
from rodeo.providers.aws import AwsHostProvider

_BASE = {
    "type": "aws",
    "region": "eu-north-1",
    "subnet_id": "subnet-1",
    "instance_type": "m8id.4xlarge",
    "ami": "ami-1",
    "security_group_ids": ["sg-1"],
}


@pytest.fixture
def ssh_home(tmp_path, monkeypatch):
    ssh_dir = tmp_path / "ssh"
    monkeypatch.setattr("rodeo.paths.rodeo_ssh_dir", lambda: ssh_dir)
    monkeypatch.setattr("rodeo.ssh_key.rodeo_ssh_dir", lambda: ssh_dir)
    return ssh_dir


# ---------- ttl ----------

def test_default_ttl_is_six_hours():
    assert deadman.ttl_hours({}) == 6
    now = datetime(2026, 10, 7, 7, 30, 15, 999, tzinfo=timezone.utc)
    assert deadman.expires_at({}, now) == datetime(2026, 10, 7, 13, 30, 15, tzinfo=timezone.utc)


def test_ttl_can_be_lengthened_for_long_workshops():
    assert deadman.ttl_hours({"ttl_hours": 10}) == 10
    assert deadman.ttl_hours({"ttl_hours": "1.5"}) == 1.5


@pytest.mark.parametrize("bad", [0, -1, "x", 169, "off"])
def test_ttl_cannot_be_disabled_or_unbounded(bad):
    with pytest.raises(ConfigError, match="ttl_hours"):
        deadman.ttl_hours({"ttl_hours": bad})


def test_provider_validate_rejects_a_bad_ttl_before_creating_anything():
    with pytest.raises(ConfigError, match="ttl_hours"):
        AwsHostProvider(ec2_client=object()).validate({**_BASE, "ttl_hours": 0})


# ---------- RunInstances ----------

def _kwargs(config, ssh_home, dry_run=False):
    p = AwsHostProvider(ec2_client=object(), sleep=lambda s: None)
    return p._run_instances_kwargs(config, host_id="primary", workshop="lab", dry_run=dry_run)


def _tags(kwargs):
    return {t["Key"]: t["Value"] for t in kwargs["TagSpecifications"][0]["Tags"]}


def test_every_instance_terminates_on_shutdown_and_carries_its_expiry(ssh_home):
    before = datetime.now(timezone.utc)
    kw = _kwargs(dict(_BASE), ssh_home)
    assert kw["InstanceInitiatedShutdownBehavior"] == "terminate"
    expiry = datetime.strptime(_tags(kw)[deadman.TAG_EXPIRES_AT], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    assert timedelta(hours=6) - timedelta(seconds=5) <= expiry - before <= timedelta(hours=6, seconds=5)


def test_userdata_arms_a_persistent_timer_at_the_tagged_expiry(ssh_home):
    kw = _kwargs({**_BASE, "ttl_hours": 2}, ssh_home)
    expiry = _tags(kw)[deadman.TAG_EXPIRES_AT]
    on_calendar = expiry.replace("T", " ").replace("Z", " UTC")
    cfg = yaml.safe_load(kw["UserData"])
    files = {f["path"]: f["content"] for f in cfg["write_files"]}
    timer = files["/etc/systemd/system/rodeo-deadman.timer"]
    assert f"OnCalendar={on_calendar}" in timer
    assert "Persistent=true" in timer
    assert "ExecStart=/usr/bin/systemctl poweroff" in files["/etc/systemd/system/rodeo-deadman.service"]
    assert files["/etc/rodeo-expires-at"].strip() == expiry
    assert ["systemctl", "enable", "--now", "rodeo-deadman.timer"] in cfg["runcmd"]
    # The existing access setup is still there.
    assert "/etc/sudoers.d/90-rodeo" in files


def test_dry_run_probe_has_the_same_shutdown_behaviour(ssh_home):
    kw = _kwargs(dict(_BASE), ssh_home, dry_run=True)
    assert kw["InstanceInitiatedShutdownBehavior"] == "terminate"
    assert "UserData" not in kw


def test_portal_vm_gets_the_switch_too(ssh_home):
    portal_cfg = {**_BASE, "instance_type": "t3.small", "nested_virtualization": False}
    kw = AwsHostProvider(ec2_client=object())._run_instances_kwargs(
        portal_cfg, host_id="portal", workshop="lab", extra_labels={"rodeo-role": "portal"}
    )
    assert kw["InstanceInitiatedShutdownBehavior"] == "terminate"
    assert deadman.TAG_EXPIRES_AT in _tags(kw)


# ---------- banner ----------

def test_aws_done_message_shows_the_expiry(tmp_path):
    from rodeo.commands import up_cmd

    msg = up_cmd._aws_done_message(
        {"vms": {"rancher": {}}}, tmp_path, "203.0.113.7", "i-1", expires_at="2026-10-07T13:30:15Z"
    )
    assert "Self-destructs:  2026-10-07T13:30:15Z" in msg
