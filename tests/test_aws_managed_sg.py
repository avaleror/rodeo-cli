"""Security-group automation: rodeo creates/reconciles/cleans up its own SG
when provider.security_group_ids is omitted, instead of requiring one
hand-created by the operator. Explicit security_group_ids stays untouched —
covered by the existing tests in test_providers_aws.py / test_aws_up.py."""
from __future__ import annotations

import pytest

from rodeo.config import ConfigError
from rodeo.providers.aws import MANAGED_SG_PORTS, AwsHostProvider, _detect_caller_ip
from rodeo.providers.base import ProvisionSpec
from tests.test_providers_aws import _FakeEC2


@pytest.fixture
def managed_ssh(tmp_path, monkeypatch):
    """Point managed ~/.rodeo/ssh at a temp dir and generate a key."""
    ssh_dir = tmp_path / "managed-ssh"
    monkeypatch.setattr("rodeo.paths.rodeo_ssh_dir", lambda: ssh_dir)
    monkeypatch.setattr("rodeo.ssh_key.rodeo_ssh_dir", lambda: ssh_dir)
    from rodeo.ssh_key import ensure_rodeo_ssh_key

    ensure_rodeo_ssh_key()
    return ssh_dir


class _FakeEC2WithSG(_FakeEC2):
    """Adds the security-group surface used by SG auto-management."""

    def __init__(self) -> None:
        super().__init__()
        self._sg_n = 0
        self.security_groups: dict[str, dict] = {
            "sg-default-vpc-1": {
                "GroupId": "sg-default-vpc-1",
                "GroupName": "default",
                "VpcId": "vpc-1",
                "Tags": [],
                "IpPermissions": [],
            }
        }
        self.dependency_violations_remaining = 0

    def describe_security_groups(self, GroupIds=None, Filters=None):
        groups = list(self.security_groups.values())
        if GroupIds:
            groups = [g for g in groups if g["GroupId"] in GroupIds]
        for f in Filters or []:
            name, values = f["Name"], set(f.get("Values") or [])
            if name == "vpc-id":
                groups = [g for g in groups if g["VpcId"] in values]
            elif name == "group-name":
                groups = [g for g in groups if g["GroupName"] in values]
            elif name.startswith("tag:"):
                key = name.split(":", 1)[1]
                groups = [
                    g
                    for g in groups
                    if any(t["Key"] == key and t["Value"] in values for t in g.get("Tags") or [])
                ]
        return {"SecurityGroups": groups}

    def create_security_group(self, GroupName, Description, VpcId, TagSpecifications=None):
        # Real EC2 rejects non-ASCII GroupDescription (caught live 2026-09-11
        # against an em dash) — enforce the same constraint here so a
        # regression fails a test instead of only a real provisioning run.
        try:
            Description.encode("ascii")
        except UnicodeEncodeError as exc:
            err = Exception("InvalidParameterValue")
            err.response = {"Error": {"Code": "InvalidParameterValue"}}  # type: ignore[attr-defined]
            raise err from exc
        for g in self.security_groups.values():
            if g["GroupName"] == GroupName and g["VpcId"] == VpcId:
                err = Exception("InvalidGroup.Duplicate")
                err.response = {"Error": {"Code": "InvalidGroup.Duplicate"}}  # type: ignore[attr-defined]
                raise err
        self._sg_n += 1
        gid = f"sg-managed-{self._sg_n:04d}"
        tags = []
        for spec in TagSpecifications or []:
            tags.extend(spec.get("Tags") or [])
        self.security_groups[gid] = {
            "GroupId": gid,
            "GroupName": GroupName,
            "VpcId": VpcId,
            "Tags": tags,
            "IpPermissions": [],
        }
        return {"GroupId": gid}

    def authorize_security_group_ingress(self, GroupId, IpPermissions):
        sg = self.security_groups[GroupId]
        for perm in IpPermissions:
            sg["IpPermissions"].append(dict(perm))
        return {}

    def revoke_security_group_ingress(self, GroupId, IpPermissions):
        sg = self.security_groups[GroupId]
        for perm in IpPermissions:
            drop = {r["CidrIp"] for r in perm.get("IpRanges") or []}
            kept = []
            for existing in sg["IpPermissions"]:
                if (
                    existing.get("FromPort") != perm.get("FromPort")
                    or existing.get("ToPort") != perm.get("ToPort")
                ):
                    kept.append(existing)
                    continue
                remaining = [r for r in existing.get("IpRanges") or [] if r["CidrIp"] not in drop]
                if remaining:
                    kept.append({**existing, "IpRanges": remaining})
            sg["IpPermissions"] = kept
        return {}

    def delete_security_group(self, GroupId):
        if self.dependency_violations_remaining > 0:
            self.dependency_violations_remaining -= 1
            err = Exception("DependencyViolation")
            err.response = {"Error": {"Code": "DependencyViolation"}}  # type: ignore[attr-defined]
            raise err
        self.security_groups.pop(GroupId, None)
        return {}

    def _cidrs_for(self, sg_id: str, port: int) -> set[str]:
        sg = self.security_groups[sg_id]
        out: set[str] = set()
        for perm in sg["IpPermissions"]:
            if perm.get("FromPort") == port and perm.get("ToPort") == port:
                out |= {r["CidrIp"] for r in perm.get("IpRanges") or []}
        return out


def _provider(**overrides) -> dict:
    cfg = {
        "type": "aws",
        "region": "eu-central-1",
        "instance_type": "i7i.8xlarge",
        "ami": "ami-x",
        "subnet_id": "subnet-1",
    }
    cfg.update(overrides)
    return cfg


def test_validate_allows_omitted_security_group_ids():
    p = AwsHostProvider(ec2_client=_FakeEC2WithSG(), sleep=lambda s: None)
    p.validate(_provider())  # must not raise


def test_validate_still_rejects_explicit_empty_list():
    p = AwsHostProvider(ec2_client=_FakeEC2WithSG(), sleep=lambda s: None)
    with pytest.raises(ConfigError, match="non-empty list"):
        p.validate(_provider(security_group_ids=[]))


def test_resolve_security_groups_returns_explicit_untouched():
    fake = _FakeEC2WithSG()
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None)
    result = p._resolve_security_groups(fake, _provider(security_group_ids=["sg-byo"]))
    assert result == ["sg-byo"]
    # No SG ever created when the operator supplied one.
    assert set(fake.security_groups) == {"sg-default-vpc-1"}


def test_resolve_without_workshop_falls_back_to_default_vpc_sg():
    fake = _FakeEC2WithSG()
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None)
    result = p._resolve_security_groups(fake, _provider())
    assert result == ["sg-default-vpc-1"]
    # Nothing new created — the default SG pre-exists and is left untagged.
    assert set(fake.security_groups) == {"sg-default-vpc-1"}


def test_resolve_with_workshop_creates_and_scopes_to_caller_ip():
    fake = _FakeEC2WithSG()
    p = AwsHostProvider(
        ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9"
    )
    result = p._resolve_security_groups(fake, _provider(), workshop="demo")
    assert len(result) == 1
    sg_id = result[0]
    assert sg_id != "sg-default-vpc-1"
    sg = fake.security_groups[sg_id]
    assert sg["GroupName"] == "rodeo-demo"
    tag_map = {t["Key"]: t["Value"] for t in sg["Tags"]}
    assert tag_map["ManagedBy"] == "rodeo"
    assert tag_map["rodeo-workshop"] == "demo"
    for port in MANAGED_SG_PORTS:
        assert fake._cidrs_for(sg_id, port) == {"203.0.113.9/32"}


def test_resolve_reuses_existing_managed_sg_for_same_workshop():
    fake = _FakeEC2WithSG()
    p = AwsHostProvider(
        ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9"
    )
    first = p._resolve_security_groups(fake, _provider(), workshop="demo")
    second = p._resolve_security_groups(fake, _provider(), workshop="demo")
    assert first == second
    assert len([g for g in fake.security_groups.values() if g["GroupName"] == "rodeo-demo"]) == 1


def test_resolve_reconciles_stale_ip_on_drift():
    fake = _FakeEC2WithSG()
    ips = iter(["203.0.113.9", "203.0.113.200"])
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: next(ips))
    sg_id = p._resolve_security_groups(fake, _provider(), workshop="demo")[0]
    for port in MANAGED_SG_PORTS:
        assert fake._cidrs_for(sg_id, port) == {"203.0.113.9/32"}

    same_sg = p._resolve_security_groups(fake, _provider(), workshop="demo")[0]
    assert same_sg == sg_id
    for port in MANAGED_SG_PORTS:
        # Old IP revoked, new one authorized — never both at once.
        assert fake._cidrs_for(sg_id, port) == {"203.0.113.200/32"}


def test_assert_available_works_without_security_group_ids():
    """The standalone pre-flight (rodeo up's capacity check, before any
    provision() call) must not blow up just because no SG was configured —
    it falls back to the VPC default SG rather than creating anything."""
    fake = _FakeEC2WithSG()
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None)
    p.assert_available(_provider(), count=1)  # must not raise
    assert set(fake.security_groups) == {"sg-default-vpc-1"}  # nothing created


def test_provision_end_to_end_uses_managed_sg(managed_ssh, monkeypatch):
    fake = _FakeEC2WithSG()
    captured: dict = {}

    def capture_run(**kwargs):
        captured.update(kwargs)
        return _FakeEC2.run_instances(fake, **kwargs)

    fake.run_instances = capture_run  # type: ignore[method-assign]
    monkeypatch.setattr(
        "rodeo.providers.aws.AwsHostProvider._wait_ssh", lambda *a, **k: None
    )
    p = AwsHostProvider(
        ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "198.51.100.4"
    )
    p.provision(
        ProvisionSpec(workshop="demo", host_ids=["primary"], ssh_user="ec2-user", wait_ssh=False),
        _provider(),
    )
    groups = captured["NetworkInterfaces"][0]["Groups"]
    assert len(groups) == 1
    sg_id = groups[0]
    assert fake.security_groups[sg_id]["GroupName"] == "rodeo-demo"
    for port in MANAGED_SG_PORTS:
        assert fake._cidrs_for(sg_id, port) == {"198.51.100.4/32"}


def test_deprovision_deletes_managed_sg_when_last_host_gone(managed_ssh, monkeypatch):
    fake = _FakeEC2WithSG()
    monkeypatch.setattr(
        "rodeo.providers.aws.AwsHostProvider._wait_ssh", lambda *a, **k: None
    )
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")
    spec = ProvisionSpec(workshop="demo", host_ids=["primary"], ssh_user="ec2-user", wait_ssh=False)
    p.provision(spec, _provider())
    sg_id = next(g["GroupId"] for g in fake.security_groups.values() if g["GroupName"] == "rodeo-demo")

    results = p.deprovision(spec, _provider())
    sg_result = next(r for r in results if r.id == "security-group")
    assert sg_result.ok is True
    assert sg_result.detail == "deleted"
    assert sg_id not in fake.security_groups


def test_deprovision_retries_through_dependency_violation(managed_ssh, monkeypatch):
    fake = _FakeEC2WithSG()
    monkeypatch.setattr(
        "rodeo.providers.aws.AwsHostProvider._wait_ssh", lambda *a, **k: None
    )
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")
    spec = ProvisionSpec(workshop="demo", host_ids=["primary"], ssh_user="ec2-user", wait_ssh=False)
    p.provision(spec, _provider())
    fake.dependency_violations_remaining = 2

    results = p.deprovision(spec, _provider())
    sg_result = next(r for r in results if r.id == "security-group")
    assert sg_result.ok is True
    assert sg_result.detail == "deleted"


def test_deprovision_never_fails_the_call_when_sg_stays_attached(managed_ssh, monkeypatch):
    fake = _FakeEC2WithSG()
    monkeypatch.setattr(
        "rodeo.providers.aws.AwsHostProvider._wait_ssh", lambda *a, **k: None
    )
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")
    spec = ProvisionSpec(workshop="demo", host_ids=["primary"], ssh_user="ec2-user", wait_ssh=False)
    p.provision(spec, _provider())
    fake.dependency_violations_remaining = 999  # never succeeds

    results = p.deprovision(spec, _provider())
    assert all(r.ok for r in results)  # instance termination + SG cleanup both non-fatal
    sg_result = next(r for r in results if r.id == "security-group")
    assert "still attached" in sg_result.detail


def test_deprovision_skips_sg_cleanup_when_sibling_host_still_alive(managed_ssh, monkeypatch):
    """Partial teardown (Fleet destroying one host) must not delete a SG
    other students' hosts still rely on."""
    fake = _FakeEC2WithSG()
    monkeypatch.setattr(
        "rodeo.providers.aws.AwsHostProvider._wait_ssh", lambda *a, **k: None
    )
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")
    p.provision(
        ProvisionSpec(
            workshop="demo", host_ids=["student-01", "student-02"], ssh_user="ec2-user", wait_ssh=False
        ),
        _provider(),
    )
    sg_id = next(g["GroupId"] for g in fake.security_groups.values() if g["GroupName"] == "rodeo-demo")

    partial_spec = ProvisionSpec(
        workshop="demo", host_ids=["student-01"], ssh_user="ec2-user", wait_ssh=False
    )
    results = p.deprovision(partial_spec, _provider())
    assert not any(r.id == "security-group" for r in results)
    assert sg_id in fake.security_groups  # untouched — student-02's host still needs it


def test_deprovision_leaves_explicit_sg_alone(managed_ssh, monkeypatch):
    fake = _FakeEC2WithSG()
    monkeypatch.setattr(
        "rodeo.providers.aws.AwsHostProvider._wait_ssh", lambda *a, **k: None
    )
    p = AwsHostProvider(ec2_client=fake, sleep=lambda s: None)
    spec = ProvisionSpec(workshop="demo", host_ids=["primary"], ssh_user="ec2-user", wait_ssh=False)
    p.provision(spec, _provider(security_group_ids=["sg-byo"]))
    results = p.deprovision(spec, _provider(security_group_ids=["sg-byo"]))
    assert not any(r.id == "security-group" for r in results)


def test_detect_caller_ip_success(monkeypatch):
    import urllib.request as urlreq

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"198.51.100.7\n"

    # A local `import urllib.request` inside _detect_caller_ip binds the same
    # already-loaded module object, so patching it here reaches that call too.
    monkeypatch.setattr(urlreq, "urlopen", lambda *a, **k: _Resp())
    assert _detect_caller_ip() == "198.51.100.7"


def test_detect_caller_ip_network_failure_raises_config_error(monkeypatch):
    import urllib.error
    import urllib.request as urlreq

    def boom(*a, **k):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(urlreq, "urlopen", boom)
    with pytest.raises(ConfigError, match="could not auto-detect"):
        _detect_caller_ip()
