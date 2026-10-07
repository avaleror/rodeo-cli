"""`rodeo up --profile <name> --target aws` without a provider block."""
from __future__ import annotations

import pytest
import yaml

from rodeo.commands import up_cmd
from rodeo.config import ConfigError
from rodeo.providers import aws as aws_mod
from rodeo.providers.aws import AwsHostProvider, default_region


class _NetEC2:
    """describe_vpcs / describe_subnets / AZ offerings for a default VPC."""

    def __init__(self, *, default_vpc=True, subnets=None, offered_zones=None):
        self.default_vpc = default_vpc
        self.subnets = subnets if subnets is not None else [
            {"SubnetId": "subnet-c", "AvailabilityZone": "eu-north-1c", "MapPublicIpOnLaunch": True},
            {"SubnetId": "subnet-a", "AvailabilityZone": "eu-north-1a", "MapPublicIpOnLaunch": True},
            {"SubnetId": "subnet-b", "AvailabilityZone": "eu-north-1b", "MapPublicIpOnLaunch": False},
        ]
        self.offered_zones = offered_zones

    def describe_vpcs(self, Filters=None):
        assert Filters == [{"Name": "is-default", "Values": ["true"]}]
        return {"Vpcs": [{"VpcId": "vpc-default"}] if self.default_vpc else []}

    def describe_subnets(self, Filters=None):
        assert {"Name": "vpc-id", "Values": ["vpc-default"]} in Filters
        return {"Subnets": list(self.subnets)}

    def describe_instance_type_offerings(self, LocationType=None, Filters=None):
        assert LocationType == "availability-zone"
        zones = self.offered_zones or [s["AvailabilityZone"] for s in self.subnets]
        return {"InstanceTypeOfferings": [{"Location": z} for z in zones]}


def test_default_region_is_eu_north_1_unless_overridden(monkeypatch):
    monkeypatch.delenv("RODEO_AWS_REGION", raising=False)
    assert default_region() == "eu-north-1"
    monkeypatch.setenv("RODEO_AWS_REGION", "us-east-1")
    assert default_region() == "us-east-1"


def test_default_subnet_picks_first_public_az():
    p = AwsHostProvider(ec2_client=_NetEC2())
    assert p.default_subnet({"region": "eu-north-1"}) == "subnet-a"


def test_default_subnet_skips_azs_without_the_instance_type():
    p = AwsHostProvider(ec2_client=_NetEC2(offered_zones=["eu-north-1c"]))
    assert p.default_subnet({"region": "eu-north-1"}, instance_type="m8id.4xlarge") == "subnet-c"


def test_default_subnet_fails_clearly_without_default_vpc():
    p = AwsHostProvider(ec2_client=_NetEC2(default_vpc=False))
    with pytest.raises(ConfigError, match="no default VPC in eu-west-1"):
        p.default_subnet({"region": "eu-west-1"})


def test_default_subnet_fails_clearly_without_public_subnet():
    p = AwsHostProvider(ec2_client=_NetEC2(subnets=[
        {"SubnetId": "subnet-x", "AvailabilityZone": "eu-north-1a", "MapPublicIpOnLaunch": False},
    ]))
    with pytest.raises(ConfigError, match="provider.subnet_id"):
        p.default_subnet({"region": "eu-north-1"})


def _lab(tmp_path, provider=None):
    lab = tmp_path / "rancher"
    lab.mkdir()
    plan = {"type": "rancher", "name": "rancher", "deployment_target": "aws"}
    if provider is not None:
        plan["provider"] = provider
    (lab / "rodeo-plan.yaml").write_text(yaml.safe_dump(plan))
    return lab


def _stub_aws(monkeypatch, seen):
    def fake_subnet(self, config, *, instance_type=""):
        seen["subnet_call"] = (config["region"], instance_type)
        return "subnet-default"

    monkeypatch.setattr(aws_mod.AwsHostProvider, "default_subnet", fake_subnet)
    monkeypatch.setattr(
        aws_mod.AwsHostProvider, "assert_available",
        lambda self, provider, count=1: seen.setdefault("checked", dict(provider)),
    )


def test_up_fills_and_persists_a_missing_provider(tmp_path, monkeypatch):
    monkeypatch.delenv("RODEO_AWS_REGION", raising=False)
    seen: dict = {}
    _stub_aws(monkeypatch, seen)
    lab = _lab(tmp_path)
    cfg = up_cmd._resolve_aws_instance_choice(
        {"type": "rancher", "name": "rancher"},
        lab=lab, lab_profile="rancher", instance_tier=None, assume_yes=True,
    )
    provider = cfg["provider"]
    assert provider["type"] == "aws"
    assert provider["region"] == "eu-north-1"
    assert provider["subnet_id"] == "subnet-default"
    assert provider["instance_type"] == "m8id.4xlarge"  # rancher's recommended tier
    assert seen["subnet_call"] == ("eu-north-1", "m8id.4xlarge")

    saved = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())["provider"]
    assert saved["region"] == "eu-north-1"
    assert saved["subnet_id"] == "subnet-default"
    assert "lab_profile" not in saved  # runtime hint only


def test_up_keeps_an_explicit_provider(tmp_path, monkeypatch):
    seen: dict = {}
    _stub_aws(monkeypatch, seen)
    explicit = {"type": "aws", "region": "eu-central-1", "subnet_id": "subnet-mine",
                "instance_type": "m7i.4xlarge"}
    lab = _lab(tmp_path, explicit)
    cfg = up_cmd._resolve_aws_instance_choice(
        {"type": "rancher", "name": "rancher", "provider": dict(explicit)},
        lab=lab, lab_profile="rancher", instance_tier=None, assume_yes=True,
    )
    assert "subnet_call" not in seen
    for key, value in explicit.items():
        assert cfg["provider"][key] == value


@pytest.mark.parametrize(
    "profile, harvester, rancher",
    [("rancher", False, True), ("rancher-test", False, True), ("suse-virt", True, True)],
)
def test_aws_done_message_lists_only_the_uis_the_lab_has(tmp_path, profile, harvester, rancher):
    from rodeo.profiles import get_profile

    cfg = {**get_profile(profile).default_cfg(), "type": profile}
    msg = up_cmd._aws_done_message(cfg, tmp_path / "lab", "203.0.113.7", "i-123")
    assert ("https://203.0.113.7:8443" in msg) is harvester
    assert ("https://203.0.113.7:30002" in msg) is rancher
    assert f"rodeo destroy --cloud --yes --config-dir {tmp_path / 'lab'}" in msg
    assert "—" not in msg


def test_aws_done_message_harvester_only_lab(tmp_path):
    cfg = {"vms": {"harvester1": {}, "harvester2": {}}}
    msg = up_cmd._aws_done_message(cfg, tmp_path, "203.0.113.7", None)
    assert "8443" in msg and "30002" not in msg
