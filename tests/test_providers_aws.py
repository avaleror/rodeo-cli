"""Tests for AWS HostProvider and fleet provision (mocked EC2)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from rodeo.config import ConfigError
from rodeo.fleet.inventory import desired_host_ids, load_inventory, merge_provisioned_hosts
from rodeo.fleet.provision import fleet_deprovision, fleet_provision
from rodeo.providers.aws import AwsHostProvider
from rodeo.providers.base import ProvisionedHost


@pytest.fixture
def managed_ssh(tmp_path, monkeypatch):
    """Point managed ~/.rodeo/ssh at a temp dir and generate a key."""
    ssh_dir = tmp_path / "managed-ssh"
    monkeypatch.setattr("rodeo.paths.rodeo_ssh_dir", lambda: ssh_dir)
    monkeypatch.setattr("rodeo.ssh_key.rodeo_ssh_dir", lambda: ssh_dir)
    from rodeo.ssh_key import ensure_rodeo_ssh_key

    ensure_rodeo_ssh_key()
    return ssh_dir


class _FakeEC2:
    def __init__(self) -> None:
        self.instances: dict[str, dict] = {}
        self._n = 0
        self.terminated: list[str] = []
        self.started: list[str] = []
        self.key_pairs: dict[str, dict] = {}
        self.imported: list[str] = []

    def describe_key_pairs(self, KeyNames=None):
        names = KeyNames or []
        pairs = [self.key_pairs[n] for n in names if n in self.key_pairs]
        if names and not pairs:
            err = Exception("InvalidKeyPair.NotFound")
            err.response = {"Error": {"Code": "InvalidKeyPair.NotFound"}}  # type: ignore[attr-defined]
            raise err
        return {"KeyPairs": pairs}

    def import_key_pair(self, KeyName, PublicKeyMaterial):
        from rodeo.ssh_key import local_pubkey_sha256, rodeo_ssh_public_key_path

        fp = local_pubkey_sha256(rodeo_ssh_public_key_path())
        self.key_pairs[KeyName] = {"KeyName": KeyName, "KeyFingerprint": fp}
        self.imported.append(KeyName)
        return {"KeyName": KeyName, "KeyFingerprint": fp}

    def describe_images(self, Owners=None, Filters=None):
        # Prefer explicit test fixtures; default Leap-like image when filtering.
        images = getattr(self, "images", None)
        if images is None:
            images = [
                {
                    "ImageId": "ami-leap-newest",
                    "Name": "openSUSE Leap 16.0 (x86_64) - v20260629",
                    "CreationDate": "2026-06-29T00:00:00.000Z",
                    "State": "available",
                    "Architecture": "x86_64",
                },
                {
                    "ImageId": "ami-leap-older",
                    "Name": "openSUSE Leap 16.0 (x86_64) - v20260101",
                    "CreationDate": "2026-01-01T00:00:00.000Z",
                    "State": "available",
                    "Architecture": "x86_64",
                },
            ]
        return {"Images": list(images)}

    def describe_instance_type_offerings(self, LocationType=None, Filters=None):
        values = []
        for f in Filters or []:
            if f.get("Name") == "instance-type":
                values = list(f.get("Values") or [])
        return {
            "InstanceTypeOfferings": [
                {"InstanceType": v, "Location": "eu-central-1"} for v in values
            ]
        }

    def describe_instances(self, Filters=None, InstanceIds=None):
        items = list(self.instances.values())
        if InstanceIds:
            items = [i for i in items if i["InstanceId"] in InstanceIds]
        if Filters:
            for f in Filters:
                name = f["Name"]
                values = set(f["Values"])
                if name.startswith("tag:"):
                    key = name.split(":", 1)[1]
                    items = [
                        i
                        for i in items
                        if any(
                            t["Key"] == key and t["Value"] in values
                            for t in i.get("Tags") or []
                        )
                    ]
                elif name == "instance-state-name":
                    items = [i for i in items if i["State"]["Name"] in values]
        return {"Reservations": [{"Instances": items}] if items else []}

    def run_instances(self, **kwargs):
        if kwargs.get("DryRun"):
            err = Exception("DryRunOperation")
            err.response = {  # type: ignore[attr-defined]
                "Error": {"Code": "DryRunOperation", "Message": "Request would have succeeded"}
            }
            raise err
        self._n += 1
        iid = f"i-{self._n:08d}"
        tags = []
        for spec in kwargs.get("TagSpecifications") or []:
            tags.extend(spec.get("Tags") or [])
        inst = {
            "InstanceId": iid,
            "State": {"Name": "pending"},
            "PublicIpAddress": "",
            "Tags": tags,
        }
        self.instances[iid] = inst
        # immediately become running with IP on next describe (simulate wait loop)
        inst["State"] = {"Name": "running"}
        inst["PublicIpAddress"] = f"203.0.113.{self._n}"
        return {"Instances": [inst]}

    def terminate_instances(self, InstanceIds):
        self.terminated.extend(InstanceIds)
        for iid in InstanceIds:
            if iid in self.instances:
                self.instances[iid]["State"] = {"Name": "shutting-down"}
        return {}

    def start_instances(self, InstanceIds):
        self.started.extend(InstanceIds)
        for iid in InstanceIds:
            if iid in self.instances:
                self.instances[iid]["State"] = {"Name": "running"}
        return {}


def _aws_workshop(tmp_path: Path, *, hosts: str = "hosts: []", count: int = 2) -> Path:
    path = tmp_path / "workshop.yaml"
    path.write_text(
        textwrap.dedent(
            f"""
            name: demo
            lab:
              dir: /root/lab
              profile: harvester
            defaults:
              ssh_user: ec2-user
            provider:
              type: aws
              count: {count}
              region: eu-central-1
              instance_type: m7i.metal-24xl
              ami: ami-abc
              subnet_id: subnet-1
              security_group_ids: [sg-1]
            {hosts}
            """
        )
    )
    return path


def test_load_inventory_allows_empty_hosts_with_provider(tmp_path):
    path = _aws_workshop(tmp_path)
    inv = load_inventory(path)
    assert inv.hosts == []
    assert inv.provider["type"] == "aws"
    assert desired_host_ids(inv) == ["student-01", "student-02"]


def test_aws_validate_requires_fields():
    p = AwsHostProvider(ec2_client=_FakeEC2(), sleep=lambda s: None)
    with pytest.raises(ConfigError, match="region"):
        p.validate({"type": "aws", "instance_type": "m7i.metal-24xl"})


def test_aws_rejects_tiny_instance_type():
    p = AwsHostProvider(ec2_client=_FakeEC2(), sleep=lambda s: None)
    with pytest.raises(ConfigError, match="too small"):
        p.validate(
            {
                "type": "aws",
                "region": "eu-central-1",
                "instance_type": "t3.xlarge",
                "ami": "ami-x",
                "key_name": "k",
                "subnet_id": "subnet-1",
                "security_group_ids": ["sg-1"],
            }
        )


def test_aws_validate_without_key_name():
    p = AwsHostProvider(ec2_client=_FakeEC2(), sleep=lambda s: None)
    p.validate(
        {
            "type": "aws",
            "region": "eu-central-1",
            "instance_type": "i7i.8xlarge",
            "subnet_id": "subnet-1",
            "security_group_ids": ["sg-1"],
        }
    )


def test_resolve_ami_explicit():
    p = AwsHostProvider(ec2_client=_FakeEC2(), sleep=lambda s: None)
    assert p.resolve_ami(p._ec2, {"ami": "ami-pinned"}) == "ami-pinned"


def test_resolve_ami_leap_filter_picks_newest():
    fake = _FakeEC2()
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None)
    assert p.resolve_ami(fake, {}) == "ami-leap-newest"
    assert (
        p.resolve_ami(fake, {"ami_name_filter": "openSUSE Leap 16.0 (x86_64)*"})
        == "ami-leap-newest"
    )


def test_resolve_ami_no_match():
    fake = _FakeEC2()
    fake.images = []
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None)
    with pytest.raises(ConfigError, match="no AMI matched"):
        p.resolve_ami(fake, {"ami_name_filter": "nope*"})


def test_aws_create_enables_nested_virt_by_default_on_i7i(managed_ssh, monkeypatch):
    fake = _FakeEC2()
    captured: dict = {}

    def capture_run(**kwargs):
        captured.update(kwargs)
        return _FakeEC2.run_instances(fake, **kwargs)

    fake.run_instances = capture_run  # type: ignore[method-assign]
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None)
    monkeypatch.setattr(
        "rodeo.providers.aws.AwsHostProvider._wait_ssh",
        lambda *a, **k: None,
    )
    from rodeo.providers.base import ProvisionSpec

    p.provision(
        ProvisionSpec(
            workshop="demo",
            host_ids=["primary"],
            ssh_user="ec2-user",
            wait_ssh=False,
        ),
        {
            "type": "aws",
            "region": "us-east-1",
            "instance_type": "i7i.8xlarge",
            "ami": "ami-x",
            "subnet_id": "subnet-1",
            "security_group_ids": ["sg-1"],
        },
    )
    assert captured.get("CpuOptions") == {"NestedVirtualization": "enabled"}
    assert captured.get("KeyName") == "rodeo"
    assert fake.imported == ["rodeo"]
    ud = captured.get("UserData") or ""
    assert "#cloud-config" in ud
    assert "NOPASSWD:ALL" in ud
    assert "PermitRootLogin prohibit-password" in ud


def test_aws_rejects_nested_virt_false_on_virtual_type():
    p = AwsHostProvider(ec2_client=_FakeEC2(), sleep=lambda s: None)
    with pytest.raises(ConfigError, match="nested_virtualization"):
        p.validate(
            {
                "type": "aws",
                "region": "us-east-1",
                "instance_type": "i7i.8xlarge",
                "ami": "ami-x",
                "key_name": "k",
                "subnet_id": "subnet-1",
                "security_group_ids": ["sg-1"],
                "nested_virtualization": False,
            }
        )


def test_ownership_tags_managed_by_rodeo():
    from rodeo.providers.base import TAG_MANAGED_BY_VALUE, ownership_tags

    tags = ownership_tags("demo", "primary")
    assert tags["ManagedBy"] == "rodeo"
    assert TAG_MANAGED_BY_VALUE == "rodeo"
    assert tags["rodeo-host-id"] == "primary"


def test_aws_provision_create_and_reuse(tmp_path, managed_ssh, monkeypatch):
    path = _aws_workshop(tmp_path, count=1)
    inv = load_inventory(path)
    fake = _FakeEC2()
    provider = AwsHostProvider(ec2_client=fake, sleep=lambda s: None)
    monkeypatch.setattr("rodeo.fleet.provision.get_provider", lambda name: provider)
    monkeypatch.setattr(
        "rodeo.providers.aws.AwsHostProvider._wait_ssh",
        lambda self, spec, host, timeout=600.0: None,
    )

    hosts = fleet_provision(inv, path, wait_ssh=False)
    assert len(hosts) == 1
    assert hosts[0].id == "student-01"
    assert hosts[0].provider_id == "i-00000001"
    assert hosts[0].labels["provision_action"] == "create"

    # second run reuses
    hosts2 = fleet_provision(inv, path, wait_ssh=False)
    assert hosts2[0].labels["provision_action"] == "reuse"
    assert len(fake.instances) == 1

    # inventory written
    reloaded = load_inventory(path)
    assert len(reloaded.hosts) == 1
    assert reloaded.hosts[0].public_ip == "203.0.113.1"


def test_aws_provision_restarts_stopped_instance(tmp_path, managed_ssh, monkeypatch):
    path = _aws_workshop(tmp_path, count=1)
    inv = load_inventory(path)
    fake = _FakeEC2()
    provider = AwsHostProvider(ec2_client=fake, sleep=lambda s: None)
    monkeypatch.setattr("rodeo.fleet.provision.get_provider", lambda name: provider)
    monkeypatch.setattr(
        "rodeo.providers.aws.AwsHostProvider._wait_ssh",
        lambda *a, **k: None,
    )
    fleet_provision(inv, path, wait_ssh=False)
    iid = next(iter(fake.instances))
    fake.instances[iid]["State"] = {"Name": "stopped"}

    hosts = fleet_provision(inv, path, wait_ssh=False)
    assert fake.started == [iid]
    assert hosts[0].labels["provision_action"] == "reuse"
    assert fake.instances[iid]["State"]["Name"] == "running"


def test_aws_deprovision_only_tagged(tmp_path, managed_ssh, monkeypatch):
    path = _aws_workshop(tmp_path, count=1)
    inv = load_inventory(path)
    fake = _FakeEC2()
    provider = AwsHostProvider(ec2_client=fake, sleep=lambda s: None)
    monkeypatch.setattr("rodeo.fleet.provision.get_provider", lambda name: provider)
    monkeypatch.setattr(
        "rodeo.providers.aws.AwsHostProvider._wait_ssh",
        lambda *a, **k: None,
    )
    fleet_provision(inv, path, wait_ssh=False)
    results = fleet_deprovision(inv)
    assert results[0].ok is True
    assert fake.terminated == ["i-00000001"]


def test_aws_missing_boto3_message(monkeypatch):
    from rodeo.providers import aws as aws_mod

    def boom():
        raise ConfigError(
            "AWS provider requires boto3 — install with: pip install 'rodeo-cli[aws]'"
        )

    monkeypatch.setattr(aws_mod, "_require_boto3", boom)
    p = AwsHostProvider(sleep=lambda s: None)
    with pytest.raises(ConfigError, match="boto3"):
        p._client({"region": "eu-central-1"})


def test_merge_preserves_order(tmp_path):
    path = tmp_path / "w.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: x
            lab: {dir: /root/lab}
            hosts:
              - id: a
                ssh: 1.1.1.1
              - id: b
                ssh: 2.2.2.2
            """
        )
    )
    merge_provisioned_hosts(
        path,
        [
            ProvisionedHost(
                id="b",
                ssh="9.9.9.9",
                public_ip="9.9.9.9",
                labels={"provider": "aws"},
                provider_id="i-b",
            )
        ],
    )
    inv = load_inventory(path)
    assert [h.id for h in inv.hosts] == ["a", "b"]
    assert inv.hosts[1].public_ip == "9.9.9.9"


def test_fleet_provision_cli(managed_ssh, monkeypatch, tmp_path):
    path = _aws_workshop(tmp_path, count=1)
    fake = _FakeEC2()
    provider = AwsHostProvider(ec2_client=fake, sleep=lambda s: None)
    monkeypatch.setattr("rodeo.fleet.provision.get_provider", lambda name: provider)
    monkeypatch.setattr(
        "rodeo.providers.aws.AwsHostProvider._wait_ssh",
        lambda *a, **k: None,
    )
    from rodeo.cli import cli

    result = CliRunner().invoke(
        cli,
        ["fleet", "provision", "-f", str(path), "--no-wait-ssh", "--output", "json"],
    )
    assert result.exit_code == 0, result.output
    assert "student-01" in result.output
