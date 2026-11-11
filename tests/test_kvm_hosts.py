"""kvm-hosts: plain KVM hosts deployed with rodeo fleet, dead-man switch optional."""
from __future__ import annotations

import pytest
import yaml
from click.testing import CliRunner

from rodeo.commands import fleet_cmd
from rodeo.commands import fleet_up_cmd as up
from rodeo.config import ConfigError
from rodeo.fleet.inventory import load_inventory, profile_plan_type
from rodeo.labseed import PROFILE_EXAMPLE
from rodeo.profiles import get_profile
from rodeo.providers import deadman
from rodeo.providers.aws import AwsHostProvider
from rodeo.providers.instance_catalog import resolve_instance_type
from rodeo.providers.remote_up import _provider_cfg
from tests.test_aws_managed_sg import _FakeEC2WithSG, _provider
from tests.test_cloud_deadman import _BASE, _kwargs, _tags, ssh_home  # noqa: F401


def _subnet(provider, *, instance_type=""):
    return "subnet-default"


def _workshop(tmp_path, profile="kvm-hosts", **provider):
    data = {
        "name": "hackweek",
        "lab": {"dir": "/root/rodeo-lab", "profile": profile, "target": "baremetal"},
        "provider": {"type": "aws", "count": 3, "region": "eu-north-1", **provider},
        "hosts": [],
    }
    path = tmp_path / "workshop.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


# ---------- the lab type ----------

def test_kvm_hosts_is_a_bundled_profile_with_no_vms():
    assert PROFILE_EXAMPLE["kvm-hosts"] == "kvm-hosts"
    assert profile_plan_type("kvm-hosts") == "kvm-hosts"
    profile = get_profile("kvm-hosts")
    assert profile.phases == ["kvm_host", "custom_scripts"]
    assert profile.default_cfg()["vms"] == {}


def test_kvm_hosts_sizes_to_m7i_2xlarge():
    itype, _ = resolve_instance_type(profile="kvm-hosts", instance_type=None, instance_tier="recommended")
    assert itype == "m7i.2xlarge"


# ---------- dead-man switch off ----------

@pytest.mark.parametrize("off", [False, "off", "never", "OFF"])
def test_off_needs_the_loader_permission(off):
    with pytest.raises(ConfigError, match="kvm-hosts"):
        deadman.ttl_hours({"ttl_hours": off})
    allowed = {"ttl_hours": off, deadman.ALLOW_OFF_KEY: True}
    assert deadman.ttl_hours(allowed) is None
    assert deadman.expires_at(allowed) is None


def test_the_permission_does_not_unbound_numbers():
    with pytest.raises(ConfigError, match="ttl_hours"):
        deadman.ttl_hours({"ttl_hours": 500, deadman.ALLOW_OFF_KEY: True})


def test_loader_allows_off_for_kvm_hosts(tmp_path):
    inv = load_inventory(_workshop(tmp_path, ttl_hours="off"))
    assert inv.provider[deadman.ALLOW_OFF_KEY] is True
    assert deadman.ttl_hours(inv.provider) is None


def test_yaml_bare_off_works_too(tmp_path):
    path = tmp_path / "workshop.yaml"
    path.write_text(
        "name: hw\nlab: {profile: kvm-hosts, dir: /root/rodeo-lab}\n"
        "provider: {type: aws, count: 3, region: eu-north-1, ttl_hours: off}\nhosts: []\n"
    )
    assert deadman.ttl_hours(load_inventory(path).provider) is None


def test_loader_refuses_off_for_any_other_lab(tmp_path):
    with pytest.raises(ConfigError, match="kvm-hosts"):
        load_inventory(_workshop(tmp_path, profile="harvester", ttl_hours="off"))


def test_a_hand_written_permission_is_ignored(tmp_path):
    path = _workshop(tmp_path, profile="harvester", ttl_hours="off", **{deadman.ALLOW_OFF_KEY: True})
    with pytest.raises(ConfigError, match="kvm-hosts"):
        load_inventory(path)
    assert deadman.ALLOW_OFF_KEY not in _provider_cfg(
        {"provider": {"type": "aws", "ttl_hours": "off", deadman.ALLOW_OFF_KEY: True}})


def test_kvm_hosts_keeps_the_switch_by_default(tmp_path):
    inv = load_inventory(_workshop(tmp_path))
    assert deadman.ttl_hours(inv.provider) == deadman.DEFAULT_TTL_HOURS


def test_hosts_without_switch_are_tagged_never_and_stop_on_poweroff(ssh_home):  # noqa: F811
    kw = _kwargs({**_BASE, "ttl_hours": "off", deadman.ALLOW_OFF_KEY: True}, ssh_home)
    assert _tags(kw)[deadman.TAG_EXPIRES_AT] == deadman.NEVER
    assert kw["InstanceInitiatedShutdownBehavior"] == "stop"
    assert deadman.TIMER_UNIT not in kw["UserData"]


# ---------- host-to-host traffic ----------

def test_host_to_host_adds_one_self_rule():
    fake = _FakeEC2WithSG()
    calls = []
    real = fake.authorize_security_group_ingress
    fake.authorize_security_group_ingress = lambda **kw: calls.append(kw) or real(**kw)
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")
    cfg = {**_provider(), "host_to_host": True}
    sg_id = p._resolve_security_groups(fake, cfg, workshop="hw")[0]
    self_rules = [c for c in calls if c["IpPermissions"][0]["IpProtocol"] == "-1"]
    assert len(self_rules) == 1
    assert self_rules[0]["IpPermissions"][0]["UserIdGroupPairs"][0]["GroupId"] == sg_id
    calls.clear()
    p._resolve_security_groups(fake, cfg, workshop="hw")
    assert not [c for c in calls if c["IpPermissions"][0]["IpProtocol"] == "-1"]


def test_no_host_to_host_rule_by_default():
    fake = _FakeEC2WithSG()
    calls = []
    real = fake.authorize_security_group_ingress
    fake.authorize_security_group_ingress = lambda **kw: calls.append(kw) or real(**kw)
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")
    p._resolve_security_groups(fake, _provider(), workshop="w")
    assert not [c for c in calls if c["IpPermissions"][0]["IpProtocol"] == "-1"]


# ---------- fleet up ----------

def test_build_inventory_for_kvm_hosts_is_a_cluster_not_a_workshop(tmp_path):
    data = up.build_inventory(profile="kvm-hosts", count=3, ttl_hours="off", default_subnet=_subnet)
    assert "portal" not in data
    assert data["provider"]["student_access"] == "operator"
    assert data["provider"]["host_to_host"] is True
    path = tmp_path / "workshop.yaml"
    path.write_text(yaml.safe_dump(data))
    assert deadman.ttl_hours(load_inventory(path).provider) is None


def test_no_deadman_is_refused_for_other_profiles(tmp_path):
    r = CliRunner().invoke(fleet_cmd.fleet_cmd, ["up", "-f", str(tmp_path / "w.yaml"), "--profile",
                                                 "harvester", "--count", "1", "--no-deadman"])
    assert r.exit_code == 2 and "only for kvm-hosts" in r.output
    assert not (tmp_path / "w.yaml").exists()


def test_no_deadman_and_ttl_hours_exclude_each_other(tmp_path):
    r = CliRunner().invoke(fleet_cmd.fleet_cmd, ["up", "-f", str(tmp_path / "w.yaml"), "--profile",
                                                 "kvm-hosts", "--count", "3", "--no-deadman",
                                                 "--ttl-hours", "10"])
    assert r.exit_code == 2 and "exclude each other" in r.output


def test_fleet_destroy_is_deprovision():
    assert fleet_cmd.fleet_cmd.get_command(None, "destroy") is fleet_cmd.fleet_deprovision_cmd
