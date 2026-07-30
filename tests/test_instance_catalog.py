"""Tests for AWS instance catalog and availability gate."""
from __future__ import annotations

import pytest

from rodeo.config import ConfigError
from rodeo.providers.aws import AwsHostProvider
from rodeo.providers.instance_catalog import (
    catalog_for_profile,
    normalize_tier,
    resolve_instance_type,
)


def test_catalog_harvester_recommended():
    offer = catalog_for_profile("harvester")["recommended"]
    assert offer.instance_type == "i7i.8xlarge"


def test_resolve_explicit_type_wins():
    itype, tier = resolve_instance_type(
        profile="harvester",
        instance_type="m7i.metal-24xl",
        instance_tier="budget",
    )
    assert itype == "m7i.metal-24xl"
    assert tier is None


def test_resolve_tier_default_recommended():
    itype, tier = resolve_instance_type(profile="rancher")
    assert tier == "recommended"
    assert itype == catalog_for_profile("rancher")["recommended"].instance_type


def test_normalize_tier_rejects_bad():
    with pytest.raises(ConfigError, match="instance_tier"):
        normalize_tier("huge")


def test_apply_instance_selection_from_tier():
    p = AwsHostProvider(sleep=lambda _s: None)
    out = p.apply_instance_selection(
        {"type": "aws", "instance_tier": "budget"},
        profile="rancher",
    )
    assert out["instance_type"] == catalog_for_profile("rancher")["budget"].instance_type
    assert out["instance_tier"] == "budget"


def test_validate_accepts_tier_without_type():
    p = AwsHostProvider(sleep=lambda _s: None)
    p.validate(
        {
            "type": "aws",
            "region": "eu-central-1",
            "instance_tier": "recommended",
            "subnet_id": "subnet-1",
            "security_group_ids": ["sg-1"],
        }
    )


def test_validate_requires_type_or_tier():
    p = AwsHostProvider(sleep=lambda _s: None)
    with pytest.raises(ConfigError, match="instance_type or provider.instance_tier"):
        p.validate(
            {
                "type": "aws",
                "region": "eu-central-1",
                "subnet_id": "subnet-1",
                "security_group_ids": ["sg-1"],
            }
        )


class _CapEC2:
    def __init__(self, *, offerings=True, dry_run_code="DryRunOperation"):
        self.offerings = offerings
        self.dry_run_code = dry_run_code
        self.images = [
            {
                "ImageId": "ami-leap",
                "Name": "openSUSE Leap 16.0 (x86_64) - v1",
                "CreationDate": "2026-01-01T00:00:00.000Z",
                "State": "available",
                "Architecture": "x86_64",
            }
        ]

    def describe_instance_type_offerings(self, LocationType=None, Filters=None):
        if self.offerings:
            return {"InstanceTypeOfferings": [{"InstanceType": "i7i.8xlarge"}]}
        return {"InstanceTypeOfferings": []}

    def describe_images(self, Owners=None, Filters=None):
        return {"Images": list(self.images)}

    def run_instances(self, **kwargs):
        assert kwargs.get("DryRun") is True
        err = Exception(self.dry_run_code)
        err.response = {  # type: ignore[attr-defined]
            "Error": {"Code": self.dry_run_code, "Message": "probe"}
        }
        raise err


def test_assert_available_ok():
    p = AwsHostProvider(ec2_client=_CapEC2(), sleep=lambda _s: None)
    p.assert_available(
        {
            "type": "aws",
            "region": "eu-central-1",
            "instance_type": "i7i.8xlarge",
            "subnet_id": "subnet-1",
            "security_group_ids": ["sg-1"],
        },
        count=1,
    )


def test_assert_available_not_offered():
    p = AwsHostProvider(ec2_client=_CapEC2(offerings=False), sleep=lambda _s: None)
    with pytest.raises(ConfigError, match="not offered in region"):
        p.assert_available(
            {
                "type": "aws",
                "region": "eu-central-1",
                "instance_type": "i7i.8xlarge",
                "subnet_id": "subnet-1",
                "security_group_ids": ["sg-1"],
            },
            count=1,
        )


def test_assert_available_insufficient_capacity():
    p = AwsHostProvider(
        ec2_client=_CapEC2(dry_run_code="InsufficientInstanceCapacity"),
        sleep=lambda _s: None,
    )
    with pytest.raises(ConfigError, match="not enough capacity"):
        p.assert_available(
            {
                "type": "aws",
                "region": "eu-central-1",
                "instance_type": "i7i.8xlarge",
                "subnet_id": "subnet-1",
                "security_group_ids": ["sg-1"],
            },
            count=1,
        )
