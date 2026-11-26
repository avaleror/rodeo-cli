"""`rodeo fleet preflight`: read-only checks before a workshop is created."""
from __future__ import annotations

import pytest
import yaml

from rodeo.fleet import preflight as pf
from rodeo.fleet.inventory import load_inventory

VCPUS = {"m8id.8xlarge": 32, "t3.small": 2, "g6.xlarge": 4, "c5.large": 2}


class FakeEC2:
    def __init__(self, running=(), offered=True):
        self.running, self.offered = list(running), offered

    def describe_instance_types(self, InstanceTypes):
        return {"InstanceTypes": [{"InstanceType": t, "VCpuInfo": {"DefaultVCpus": VCPUS[t]}} for t in InstanceTypes]}

    def get_paginator(self, name):
        assert name == "describe_instances"
        running = self.running

        class P:
            def paginate(self, **kw):
                return [{"Reservations": [{"Instances": [{"InstanceType": t} for t in running]}]}]
        return P()

    def describe_subnets(self, SubnetIds):
        return {"Subnets": [{"AvailabilityZone": "eu-north-1a"}]}

    def describe_instance_type_offerings(self, **kw):
        return {"InstanceTypeOfferings": [{"InstanceType": "x"}] if self.offered else []}


class FakeQuotas:
    def __init__(self, value=1152, fail=False):
        self.value, self.fail, self.codes = value, fail, []

    def get_service_quota(self, ServiceCode, QuotaCode):
        self.codes.append(QuotaCode)
        if self.fail:
            raise RuntimeError("AccessDenied")
        return {"Quota": {"Value": float(self.value)}}


def _inventory(tmp_path, *, portal=False, hosts=0):
    data = {"name": "w", "lab": {"dir": "/root/rodeo-lab", "profile": "virt-rodeo"},
            "provider": {"type": "aws", "count": 3, "region": "eu-north-1", "subnet_id": "subnet-1",
                         "instance_type": "m8id.8xlarge", "student_access": "open" if portal else "operator"},
            "hosts": [{"id": f"student-{i:02d}", "ssh": f"198.51.100.{i}", "labels": {"provider_id": f"i-{i}"}}
                      for i in range(1, hosts + 1)]}
    if portal:
        data["portal"] = {"enabled": True, "mode": "open"}
    p = tmp_path / "workshop.yaml"
    p.write_text(yaml.safe_dump(data))
    return load_inventory(p)


def _run(inv, **kw):
    kw.setdefault("ec2", FakeEC2())
    kw.setdefault("quotas", FakeQuotas())
    kw.setdefault("session", lambda: (None, False))
    return {c.name: c for c in pf.preflight(inv, **kw)}


@pytest.mark.parametrize("itype,bucket", [("m8id.8xlarge", "standard"), ("t3.small", "standard"),
                                          ("g6.xlarge", "g"), ("vt1.3xlarge", "g"), ("p5.48xlarge", "p"),
                                          ("inf2.xlarge", "inf"), ("trn1.2xlarge", "trn"),
                                          ("dl1.24xlarge", "dl"), ("x2idn.16xlarge", "x")])
def test_quota_bucket(itype, bucket):
    assert pf.quota_bucket(itype) == bucket


def test_fits_counts_the_planned_hosts_and_the_portal(tmp_path):
    c = _run(_inventory(tmp_path, portal=True), ec2=FakeEC2(running=["m8id.8xlarge"]))
    assert c["vCPU quota"].state == "ok"
    assert "need 98, free 1120" in c["vCPU quota"].detail  # 3 x 32 + t3.small portal


def test_hosts_already_provisioned_need_nothing_more(tmp_path):
    # Like `fleet provision`: listed hosts are the plan, provisioned ones exist already.
    c = _run(_inventory(tmp_path, hosts=2))
    assert "need 0," in c["vCPU quota"].detail


def test_count_plans_a_bigger_event_and_fails_over_quota(tmp_path):
    q = FakeQuotas()
    c = _run(_inventory(tmp_path), count=40, quotas=q)
    assert c["vCPU quota"].state == "fail" and "L-1216C47A" in c["vCPU quota"].detail
    assert q.codes == ["L-1216C47A"]


def test_close_to_the_quota_warns(tmp_path):
    assert _run(_inventory(tmp_path), count=30)["vCPU quota"].state == "warn"  # 960 of 1152


def test_no_quota_permission_is_a_warning(tmp_path):
    assert _run(_inventory(tmp_path), quotas=FakeQuotas(fail=True))["vCPU quota"].state == "warn"


def test_type_not_offered_in_the_subnet_az_fails(tmp_path):
    c = _run(_inventory(tmp_path), ec2=FakeEC2(offered=False))
    assert c["instance type"].state == "fail" and "eu-north-1a" in c["instance type"].detail


@pytest.mark.parametrize("session,state", [((None, False), "ok"), ((900, True), "ok"),
                                           ((900, False), "warn"), ((7200, False), "ok")])
def test_session(tmp_path, session, state):
    assert _run(_inventory(tmp_path), session=lambda: session)["AWS session"].state == state


def test_player_checks_only_with_a_player_block(tmp_path):
    assert "DNS zone" not in _run(_inventory(tmp_path))
