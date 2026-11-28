"""rodeo cloud prune: security groups and key pairs left by labs that are gone.

A dead-man switch terminates instances and nothing else. The prune removes the
rest, but only your own tagged resources, only for deployments no instance
carries any more, and only past a grace period.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from click.testing import CliRunner

from rodeo.providers.aws import TAG_CREATED_AT, AwsHostProvider, ttl_alarm_periods

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
            elif name == "instance-id":
                if res["InstanceId"] not in values:
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

    def terminate_instances(self, InstanceIds):  # only for expired instances
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


# The backstop for a dead-man switch that did not fire: a hung host, a boot
# script that never armed the timer. Only your own instances, past expiry + grace.

def _exp(iid, hours_ago, *, owner=ME, state="running", managed=True, stamp=None):
    tags = _tags(dep=f"d-{iid}", owner=owner, ws=f"ws-{iid}", managed=managed)
    tags.append({"Key": "rodeo-expires-at", "Value": stamp if stamp is not None else _stamp(hours_ago)})
    return {"InstanceId": iid, "State": {"Name": state}, "Tags": tags}


def _expired_world():
    return FakeEc2(instances=[
        _exp("i-late", 3),                              # 3 h past expiry: terminated
        _exp("i-stopped", 3, state="stopped"),          # stopped still bills its disks
        _exp("i-grace", 0.2),                           # 12 min past: the timer may still be at work
        _exp("i-future", -2),                           # not expired yet
        _exp("i-never", 0, stamp="never"),              # kvm-hosts without a switch
        _exp("i-garbled", 0, stamp="soon"),
        _exp("i-theirs", 3, owner=TEAMMATE),
        _exp("i-bare", 3, managed=False),
        _exp("i-gone", 3, state="terminated"),
    ])


def test_expired_lists_only_your_instances_past_expiry_and_grace():
    rows = _aws(_expired_world()).expired({"region": "eu-north-1"}, now=NOW)
    assert [r["instance"] for r in rows] == ["i-late", "i-stopped"]
    assert rows[0]["workshop"] == "ws-i-late"


def test_terminate_expired_dry_run_then_for_real():
    fake = _expired_world()
    dry = _aws(fake).terminate_expired({"region": "eu-north-1"}, dry_run=True)
    assert {r.provider_id for r in dry} == {"i-late", "i-stopped"}
    assert all(r.detail.startswith("would terminate") for r in dry) and fake.terminated == []
    done = _aws(fake).terminate_expired({"region": "eu-north-1"})
    assert sorted(fake.terminated) == ["i-late", "i-stopped"]
    assert all(r.detail.startswith("terminated") for r in done)


def test_cloud_prune_terminates_expired_unless_kept(monkeypatch):
    from rodeo.cli import cli

    fake = _expired_world()
    monkeypatch.setattr("rodeo.commands.cloud_cmd.AwsHostProvider", lambda: _aws(fake))
    res = CliRunner().invoke(cli, ["cloud", "prune", "--keep-expired", "--region", "eu-north-1"])
    assert res.exit_code == 0, res.output
    assert fake.terminated == []
    res = CliRunner().invoke(cli, ["cloud", "prune", "--region", "eu-north-1"])
    assert res.exit_code == 0, res.output
    assert "i-late" in res.output and sorted(fake.terminated) == ["i-late", "i-stopped"]


def test_cloud_prune_dns_zone(monkeypatch):
    from rodeo.cli import cli

    fake = FakeEc2()
    seen = {}

    def prune(zone, *, default_region, dry_run=False):
        seen.update(zone=zone, region=default_region, dry_run=dry_run)
        return [f"dead.{zone}"]

    monkeypatch.setattr("rodeo.commands.cloud_cmd.AwsHostProvider", lambda: _aws(fake))
    monkeypatch.setattr("rodeo.fleet.dns.prune", prune)
    res = CliRunner().invoke(cli, ["cloud", "prune", "--dry-run", "--dns-zone", "example.com",
                                   "--region", "eu-north-1"])
    assert res.exit_code == 0, res.output
    assert seen == {"zone": "example.com", "region": "eu-north-1", "dry_run": True}
    assert "dead.example.com" in res.output and "would remove" in res.output


# --- AWS-side TTL alarm: the backstop for a guest timer that never fires ---


class FakeCw:
    def __init__(self, alarms=(), fail=False):
        self.alarms = {a["AlarmName"]: a for a in alarms}
        self.fail = fail
        self.put = []

    def put_metric_alarm(self, **kw):
        if self.fail:
            err = Exception("denied")
            err.response = {"Error": {"Code": "AccessDenied"}}  # type: ignore[attr-defined]
            raise err
        self.put.append(kw)

    def delete_alarms(self, AlarmNames):
        for n in AlarmNames:
            self.alarms.pop(n, None)

    def list_tags_for_resource(self, ResourceARN):
        return {"Tags": next(a["Tags"] for a in self.alarms.values() if a["AlarmArn"] == ResourceARN)}

    def get_paginator(self, op):
        assert op == "describe_alarms"
        fake = self

        class P:
            def paginate(self, AlarmNamePrefix, AlarmTypes):
                yield {"MetricAlarms": [a for n, a in fake.alarms.items() if n.startswith(AlarmNamePrefix)]}
        return P()


def _alarm(iid, owner=ME):
    return {"AlarmName": f"rodeo-ttl-{iid}", "AlarmArn": f"arn:alarm:{iid}",
            "Tags": [{"Key": "ManagedBy", "Value": "rodeo"}, {"Key": "rodeo-owner", "Value": owner}]}


def _aws_cw(fake, cw):
    return AwsHostProvider(ec2_client=fake, cloudwatch_client=cw, sleep=lambda s: None,
                           caller_ip_fn=lambda: "203.0.113.9")


def test_ttl_alarm_waits_two_periods_past_the_hours_left():
    # The first period is the partial launch hour, so hours + 2 periods fire
    # about an hour after the guest timer.
    assert ttl_alarm_periods(_stamp(-4), now=NOW) == 6
    assert ttl_alarm_periods(_stamp(-3.5), now=NOW) == 6
    assert ttl_alarm_periods(_stamp(-166), now=NOW) == 168
    assert ttl_alarm_periods(_stamp(-167), now=NOW) is None  # past CloudWatch's 7 days
    assert ttl_alarm_periods("never", now=NOW) is None
    assert ttl_alarm_periods("", now=NOW) is None


def _launch_kwargs(expires):
    tags = [{"Key": "ManagedBy", "Value": "rodeo"}, {"Key": "rodeo-owner", "Value": ME},
            {"Key": "rodeo-workshop", "Value": "ws"}, {"Key": "rodeo-deployment", "Value": "d1"},
            {"Key": "rodeo-expires-at", "Value": expires}, {"Key": "Name", "Value": "ws-student-01"}]
    return {"TagSpecifications": [{"ResourceType": "instance", "Tags": tags}]}


def test_launch_arms_a_terminate_alarm():
    cw = FakeCw()
    _aws_cw(FakeEc2(), cw)._arm_ttl_alarm({"region": "eu-north-1"}, {"InstanceId": "i-new"},
                                          _launch_kwargs(_stamp(-6)))
    [kw] = cw.put
    assert kw["AlarmName"] == "rodeo-ttl-i-new"
    assert kw["Dimensions"] == [{"Name": "InstanceId", "Value": "i-new"}]
    assert (kw["Period"], kw["EvaluationPeriods"], kw["DatapointsToAlarm"]) == (3600, 8, 8)
    assert kw["Statistic"] == "SampleCount" and kw["TreatMissingData"] == "notBreaching"
    assert kw["AlarmActions"] == ["arn:aws:automate:eu-north-1:ec2:terminate"]
    assert {t["Key"]: t["Value"] for t in kw["Tags"]} == {
        "ManagedBy": "rodeo", "rodeo-owner": ME, "rodeo-workshop": "ws", "rodeo-deployment": "d1"}


def test_no_alarm_without_a_switch_and_a_failed_alarm_only_warns(capsys):
    cw = FakeCw()
    _aws_cw(FakeEc2(), cw)._arm_ttl_alarm({"region": "eu-north-1"}, {"InstanceId": "i-k"},
                                          _launch_kwargs("never"))
    assert cw.put == []
    _aws_cw(FakeEc2(), FakeCw(fail=True))._arm_ttl_alarm(
        {"region": "eu-north-1"}, {"InstanceId": "i-x"}, _launch_kwargs(_stamp(-6)))
    assert "no AWS-side TTL alarm for i-x (AccessDenied)" in capsys.readouterr().err


def test_terminating_drops_the_alarm():
    cw = FakeCw([_alarm("i-late"), _alarm("i-future")])
    _aws_cw(_expired_world(), cw).terminate_expired({"region": "eu-north-1"})
    assert set(cw.alarms) == {"rodeo-ttl-i-future"}


def test_prune_deletes_only_your_alarms_whose_instance_is_gone():
    fake = FakeEc2(instances=[_inst("live0001"), _inst("gone0001", state="terminated")])
    cw = FakeCw([_alarm("i-live0001"), _alarm("i-gone0001"), _alarm("i-vanished"),
                 _alarm("i-theirs", owner=TEAMMATE)])
    dry = _aws_cw(fake, cw).prune_orphans({"region": "eu-north-1"}, dry_run=True)
    assert {r.provider_id for r in dry if r.id == "ttl-alarm"} == {"rodeo-ttl-i-gone0001",
                                                                   "rodeo-ttl-i-vanished"}
    assert len(cw.alarms) == 4
    _aws_cw(fake, cw).prune_orphans({"region": "eu-north-1"})
    assert set(cw.alarms) == {"rodeo-ttl-i-live0001", "rodeo-ttl-i-theirs"}
