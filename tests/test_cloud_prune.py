"""rodeo cloud prune: security groups and key pairs left by labs that are gone.

A dead-man switch terminates instances and nothing else. The prune removes the
rest, but only your own tagged resources, only for deployments no instance
carries any more, and only past a grace period.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from click.testing import CliRunner

from rodeo.providers.aws import TAG_CREATED_AT, AwsHostProvider

ME = "arn:aws:sts::123456789012:assumed-role/RodeoTest/tester@example.com"  # conftest owner
TEAMMATE = "arn:aws:sts::123456789012:assumed-role/RodeoTest/bob@example.com"
NOW = time.time()


def _stamp(hours_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - hours_ago * 3600))


def _tags(**kw) -> list[dict[str, str]]:
    names = {"dep": "rodeo-deployment", "owner": "rodeo-owner", "ws": "rodeo-workshop",
             "created": TAG_CREATED_AT}
    out = [{"Key": "ManagedBy", "Value": "rodeo"}] if kw.pop("managed", True) else []
    return out + [{"Key": names[k], "Value": v} for k, v in kw.items()]


class FakeEc2:
    def __init__(self, instances=(), groups=(), pairs=()):
        self.instances = list(instances)
        self.groups = {g["GroupId"]: g for g in groups}
        self.pairs = {p["KeyName"]: p for p in pairs}
        self.attached = set()
        self.terminated = []

    @staticmethod
    def _match(res, filters):
        tags = {t["Key"]: t["Value"] for t in res.get("Tags") or []}
        for f in filters:
            name, values = f["Name"], f["Values"]
            if name == "tag-key":
                if not set(values) & set(tags):
                    return False
            elif name.startswith("tag:"):
                if tags.get(name[4:]) not in values:
                    return False
            elif name == "instance-state-name":
                if res["State"]["Name"] not in values:
                    return False
        return True

    def get_paginator(self, op):
        fake = self

        class P:
            def paginate(self, Filters):
                if op == "describe_instances":
                    yield {"Reservations": [{"Instances": [i for i in fake.instances
                                                           if fake._match(i, Filters)]}]}
                elif op == "describe_security_groups":
                    yield {"SecurityGroups": [g for g in fake.groups.values() if fake._match(g, Filters)]}
                else:
                    raise AssertionError(op)
        return P()

    def describe_instances(self, Filters):
        return {"Reservations": [{"Instances": [i for i in self.instances if self._match(i, Filters)]}]}

    def describe_key_pairs(self, Filters):
        return {"KeyPairs": [p for p in self.pairs.values() if self._match(p, Filters)]}

    def delete_security_group(self, GroupId):
        if GroupId in self.attached:
            err = Exception("DependencyViolation")
            err.response = {"Error": {"Code": "DependencyViolation"}}  # type: ignore[attr-defined]
            raise err
        del self.groups[GroupId]

    def delete_key_pair(self, KeyName):
        del self.pairs[KeyName]

    def terminate_instances(self, InstanceIds):  # must never be called
        self.terminated += InstanceIds


def _inst(dep, state="running", **kw):
    return {"InstanceId": f"i-{dep}", "State": {"Name": state}, "Tags": _tags(dep=dep, owner=ME, **kw)}


def _sg(gid, dep, *, owner=ME, hours=5.0, **kw):
    created = {} if hours is None else {"created": _stamp(hours)}
    return {"GroupId": gid, "Tags": _tags(dep=dep, owner=owner, ws=f"ws-{dep}", **created, **kw)}


def _pair(name, dep, *, owner=ME, hours=5.0, **kw):
    when = datetime.fromtimestamp(NOW, timezone.utc) - timedelta(hours=hours)
    return {"KeyName": name, "CreateTime": when, "Tags": _tags(dep=dep, owner=owner, ws=f"ws-{dep}", **kw)}


def _world():
    return FakeEc2(
        instances=[_inst("live0001"), _inst("stop0001", state="stopped"),
                   _inst("down0001", state="shutting-down"), _inst("gone0001", state="terminated"),
                   # A live instance without ManagedBy still protects its deployment.
                   {"InstanceId": "i-x", "State": {"Name": "running"},
                    "Tags": _tags(dep="bare0001", owner=ME, managed=False)}],
        groups=[_sg("sg-live", "live0001"), _sg("sg-stop", "stop0001"), _sg("sg-down", "down0001"),
                _sg("sg-bare", "bare0001"),
                _sg("sg-gone", "gone0001"),                       # orphan: pruned
                _sg("sg-legacy", "dead0002", hours=None),          # no stamp: old, pruned
                _sg("sg-young", "dead0003", hours=0.5),            # still launching: kept
                _sg("sg-theirs", "dead0004", owner=TEAMMATE),      # a teammate's: kept
                {"GroupId": "sg-nodep", "Tags": _tags(owner=ME)},  # no deployment id: kept
                {"GroupId": "sg-hand", "Tags": [{"Key": "Name", "Value": "mine"}]}],
        pairs=[_pair("rodeo-gone", "gone0001"), _pair("rodeo-live", "live0001"),
               _pair("rodeo-young", "dead0003", hours=0.5), _pair("rodeo-theirs", "dead0004", owner=TEAMMATE),
               {"KeyName": "rodeo", "CreateTime": datetime(2026, 1, 1, tzinfo=timezone.utc), "Tags": []}],
    )


def _aws(fake):
    return AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")


def test_orphans_are_only_your_tagged_leftovers_past_the_grace():
    rows = _aws(_world()).orphans({"region": "eu-north-1"}, now=NOW)
    assert {r["deployment"]: (r["security_groups"], r["key_pairs"]) for r in rows} == {
        "gone0001": (["sg-gone"], ["rodeo-gone"]),
        "dead0002": (["sg-legacy"], []),
    }


def test_prune_deletes_them_never_an_instance_and_dry_run_deletes_nothing():
    fake = _world()
    dry = _aws(fake).prune_orphans({"region": "eu-north-1"}, dry_run=True)
    assert len(dry) == 3 and all("would delete" in r.detail for r in dry)
    assert "sg-gone" in fake.groups and "rodeo-gone" in fake.pairs

    fake.attached.add("sg-legacy")  # an ENI still holds it
    done = {r.provider_id: r.detail for r in _aws(fake).prune_orphans({"region": "eu-north-1"})}
    assert done["sg-gone"].startswith("deleted") and done["rodeo-gone"].startswith("deleted")
    assert done["sg-legacy"] == "kept (DependencyViolation)"
    assert "sg-gone" not in fake.groups and "rodeo-gone" not in fake.pairs
    assert {"sg-live", "sg-stop", "sg-down", "sg-bare", "sg-young", "sg-theirs", "sg-nodep",
            "sg-hand", "sg-legacy"} <= set(fake.groups)
    assert {"rodeo-live", "rodeo-young", "rodeo-theirs", "rodeo"} <= set(fake.pairs)
    assert fake.terminated == []


def test_new_security_groups_are_stamped():
    from tests.test_aws_managed_sg import _FakeEC2WithSG

    fake = _FakeEC2WithSG()
    _aws(fake)._create_sg(fake, vpc_id="vpc-1", name="rodeo-x-1", description="x",
                          tags={"ManagedBy": "rodeo"})
    (sg,) = [g for g in fake.security_groups.values() if g.get("GroupName") == "rodeo-x-1"]
    stamp = {t["Key"]: t["Value"] for t in sg["Tags"]}[TAG_CREATED_AT]
    assert abs(time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")) - time.mktime(time.gmtime())) < 120


def test_cloud_prune_command(monkeypatch):
    from rodeo.cli import cli

    fake = _world()
    monkeypatch.setattr("rodeo.commands.cloud_cmd.AwsHostProvider", lambda: _aws(fake))
    res = CliRunner().invoke(cli, ["cloud", "prune", "--dry-run", "--region", "eu-north-1"])
    assert res.exit_code == 0, res.output
    assert "sg-gone" in res.output and "rodeo-gone" in res.output and "sg-live" not in res.output
    assert "sg-gone" in fake.groups
    res = CliRunner().invoke(cli, ["cloud", "prune", "--region", "eu-north-1"])
    assert res.exit_code == 0 and "sg-gone" not in fake.groups
    res = CliRunner().invoke(cli, ["cloud", "prune", "--region", "eu-north-1"])
    assert "Nothing to prune" in res.output
