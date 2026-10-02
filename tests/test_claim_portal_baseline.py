"""Characterisation tests pinning today's behaviour before the claim portal
(F5) work touches the AWS provider or the inventory loader.

See docs/claim-portal-plan.md section 1 (no-regression contract) and the
F5.0 baseline list. These tests describe what ``main`` does now; a later
phase that needs one of them to change must explain why in its PR.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from rodeo.fleet.inventory import load_inventory
from rodeo.providers.aws import MANAGED_SG_PORTS, AwsHostProvider
from rodeo.providers.base import ProvisionSpec
from tests.test_aws_managed_sg import _FakeEC2WithSG, _provider
from tests.test_aws_managed_sg import managed_ssh  # noqa: F401 (fixture)

REPO = Path(__file__).resolve().parent.parent


class _CountingEC2(_FakeEC2WithSG):
    def __init__(self) -> None:
        super().__init__()
        self.authorize_calls = 0
        self.revoke_calls = 0

    def authorize_security_group_ingress(self, GroupId, IpPermissions):
        self.authorize_calls += 1
        return super().authorize_security_group_ingress(GroupId, IpPermissions)

    def revoke_security_group_ingress(self, GroupId, IpPermissions):
        self.revoke_calls += 1
        return super().revoke_security_group_ingress(GroupId, IpPermissions)


def _all_cidrs(fake: _FakeEC2WithSG, sg_id: str) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for perm in fake.security_groups[sg_id]["IpPermissions"]:
        port = perm["FromPort"]
        out.setdefault(port, set()).update(r["CidrIp"] for r in perm.get("IpRanges") or [])
    return out


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr("rodeo.providers.aws.AwsHostProvider._wait_ssh", lambda *a, **k: None)


# -- security group rules (R3) ------------------------------------------------


def test_default_managed_sg_has_only_operator_cidr_on_exactly_the_managed_ports():
    assert MANAGED_SG_PORTS == (22, 8443, 30002)
    fake = _FakeEC2WithSG()
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")
    sg_id = p._resolve_security_groups(fake, _provider(), workshop="demo")[0]
    assert _all_cidrs(fake, sg_id) == {port: {"203.0.113.9/32"} for port in MANAGED_SG_PORTS}


def test_managed_sg_rerun_from_same_ip_makes_no_ingress_calls():
    fake = _CountingEC2()
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")
    p._resolve_security_groups(fake, _provider(), workshop="demo")
    fake.authorize_calls = fake.revoke_calls = 0
    p._resolve_security_groups(fake, _provider(), workshop="demo")
    assert (fake.authorize_calls, fake.revoke_calls) == (0, 0)


def test_managed_sg_reconcile_revokes_a_hand_added_world_cidr():
    """Provision owns the managed SG: any extra source on a managed port,
    including 0.0.0.0/0 opened by hand, is revoked on the next run."""
    fake = _FakeEC2WithSG()
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")
    sg_id = p._resolve_security_groups(fake, _provider(), workshop="demo")[0]
    fake.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            {"IpProtocol": "tcp", "FromPort": 8443, "ToPort": 8443,
             "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
        ],
    )
    p._resolve_security_groups(fake, _provider(), workshop="demo")
    assert fake._cidrs_for(sg_id, 8443) == {"203.0.113.9/32"}


# -- SSH key planting (R2) ------------------------------------------------------


def test_provision_plants_rodeo_key_once_per_host_when_waiting_for_ssh(
    managed_ssh, no_wait, monkeypatch  # noqa: F811
):
    planted: list[str] = []
    monkeypatch.setattr(
        "rodeo.providers.aws.plant_rodeo_ssh_key", lambda inv, fh: planted.append(fh.id)
    )
    fake = _FakeEC2WithSG()
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")
    spec = ProvisionSpec(workshop="demo", host_ids=["s1", "s2"], ssh_user="ec2-user", wait_ssh=True)
    p.provision(spec, _provider())
    assert planted == ["s1", "s2"]


def test_provision_without_ssh_wait_plants_no_key(managed_ssh, no_wait, monkeypatch):  # noqa: F811
    planted: list[str] = []
    monkeypatch.setattr(
        "rodeo.providers.aws.plant_rodeo_ssh_key", lambda inv, fh: planted.append(fh.id)
    )
    fake = _FakeEC2WithSG()
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")
    spec = ProvisionSpec(workshop="demo", host_ids=["s1"], ssh_user="ec2-user", wait_ssh=False)
    p.provision(spec, _provider())
    assert planted == []


# -- deprovision target selection (R1) ------------------------------------------


def test_deprovision_never_touches_another_workshops_instances(managed_ssh, no_wait):  # noqa: F811
    fake = _FakeEC2WithSG()
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")
    mk = lambda ws: ProvisionSpec(  # noqa: E731
        workshop=ws, host_ids=["s1"], ssh_user="ec2-user", wait_ssh=False
    )
    p.provision(mk("demo"), _provider())
    p.provision(mk("other"), _provider())
    other_ids = {
        i["InstanceId"]
        for i in fake.instances.values()
        if any(t["Key"] == "rodeo-workshop" and t["Value"] == "other" for t in i["Tags"])
    }
    results = p.deprovision(mk("demo"), _provider())
    assert not other_ids & set(fake.terminated)
    assert [r.id for r in results if r.id != "security-group"] == ["s1"]


# -- inventory loader accepts every documented example --------------------------


def _doc_inventories() -> list[tuple[str, dict]]:
    out = []
    for rel in ("docs/examples/workshop.md", "docs/fleet.md"):
        text = (REPO / rel).read_text()
        for i, block in enumerate(re.findall(r"```yaml\n(.*?)```", text, re.S)):
            try:
                data = yaml.safe_load(block)
            except yaml.YAMLError:
                continue
            if isinstance(data, dict) and "lab" in data and ("hosts" in data or "provider" in data):
                out.append((f"{rel}#{i}", data))
    return out


_DOC_INVENTORIES = _doc_inventories()


def test_doc_inventory_examples_were_found():
    assert len(_DOC_INVENTORIES) >= 3


@pytest.mark.parametrize("label,data", _DOC_INVENTORIES, ids=[x[0] for x in _DOC_INVENTORIES])
def test_load_inventory_accepts_documented_example(tmp_path, label, data):
    path = tmp_path / "workshop.yaml"
    path.write_text(yaml.safe_dump(data))
    inv = load_inventory(path)
    assert inv.name
