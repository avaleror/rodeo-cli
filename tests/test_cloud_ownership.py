"""Issue #175: many people share one AWS account and reuse lab names.

Every resource carries rodeo-owner (caller ARN or RODEO_OWNER) and
rodeo-deployment (8 hex chars per deployment). Destroy matches on those two,
never on the lab name alone, so nobody can remove a teammate's lab by accident.
"""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from rodeo.config import ConfigError
from rodeo.providers.aws import AwsHostProvider, caller_owner
from rodeo.providers.base import ProvisionSpec, check_owner
from rodeo.ssh_key import lab_ssh_dir, load_lab_ssh_key, new_lab_ssh_key
from tests.test_aws_managed_sg import _FakeEC2WithSG, _provider

ALICE = "arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_PowerUser_abc/alice@example.com"
BOB = "arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_PowerUser_abc/bob@example.com"


@pytest.fixture
def machines(tmp_path, monkeypatch):
    """Switch between two laptops: each has its own ~/.rodeo/ssh and owner."""
    monkeypatch.setattr("rodeo.providers.aws.AwsHostProvider._wait_ssh", lambda *a, **k: None)

    def use(name: str, owner: str) -> None:
        ssh_dir = tmp_path / name / "ssh"
        monkeypatch.setattr("rodeo.paths.rodeo_ssh_dir", lambda: ssh_dir)
        monkeypatch.setattr("rodeo.ssh_key.rodeo_ssh_dir", lambda: ssh_dir)
        monkeypatch.setenv("RODEO_OWNER", owner)

    return use


def _aws(fake) -> AwsHostProvider:
    return AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")


def _spec(workshop="rancher-test", hosts=("primary",), **kw) -> ProvisionSpec:
    return ProvisionSpec(workshop=workshop, host_ids=list(hosts), ssh_user="ec2-user",
                         wait_ssh=False, **kw)


def _tags(res) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in res.get("Tags") or []}


def _live(fake):
    return [i for i in fake.instances.values() if i["State"]["Name"] == "running"]


def _only_live_id(fake, owner):
    ids = [i["InstanceId"] for i in _live(fake) if _tags(i).get("rodeo-owner") == owner]
    assert len(ids) == 1
    return ids[0]


# -- the bug this issue fixes ----------------------------------------------------


def test_same_lab_name_two_owners_destroy_only_removes_your_own(machines):
    fake = _FakeEC2WithSG()
    machines("alice-laptop", ALICE)
    _aws(fake).provision(_spec(), _provider())
    alice_iid = _only_live_id(fake, ALICE)

    machines("bob-laptop", BOB)
    _aws(fake).provision(_spec(), _provider())  # no "rename your plan" error
    bob_iid = _only_live_id(fake, BOB)
    results = _aws(fake).deprovision(_spec(), _provider())

    assert bob_iid in fake.terminated and alice_iid not in fake.terminated
    assert any(r.id == "security-group" and r.detail == "deleted" for r in results)
    alice_sgs = [g for g in fake.security_groups.values()
                 if _tags(g).get("rodeo-owner") == ALICE]
    assert len(alice_sgs) == 1  # Alice's SG is still there


def test_destroy_without_key_or_recorded_ids_refuses_and_touches_nothing(machines):
    fake = _FakeEC2WithSG()
    machines("alice-laptop", ALICE)
    _aws(fake).provision(_spec(), _provider())
    machines("bob-laptop", BOB)  # Bob never deployed rancher-test here
    with pytest.raises(ConfigError, match="rodeo cloud ls"):
        _aws(fake).deprovision(_spec(), _provider())
    assert fake.terminated == []


def test_same_owner_two_arns_sharing_a_session_name_are_two_owners(machines):
    """The owner is the whole ARN: a shared role session name like aws-cli
    must not make two callers look like one."""
    one = "arn:aws:sts::123456789012:assumed-role/TeamA/aws-cli"
    two = "arn:aws:sts::123456789012:assumed-role/TeamB/aws-cli"
    fake = _FakeEC2WithSG()
    machines("a", one)
    _aws(fake).provision(_spec(), _provider())
    first = _only_live_id(fake, one)
    machines("b", two)
    _aws(fake).provision(_spec(), _provider())
    _aws(fake).deprovision(_spec(), _provider())
    assert first not in fake.terminated


def test_destroy_without_key_uses_only_recorded_instance_ids(machines):
    fake = _FakeEC2WithSG()
    machines("alice-laptop", ALICE)
    _aws(fake).provision(_spec(), _provider())
    mine = _only_live_id(fake, ALICE)
    machines("bob-laptop", BOB)
    _aws(fake).provision(_spec(), _provider())
    machines("alice-laptop", ALICE)
    import shutil

    shutil.rmtree(lab_ssh_dir("rancher-test"))  # key lost, host state kept
    _aws(fake).deprovision(_spec(known_instance_ids=[mine]), _provider())
    assert fake.terminated == [mine]


def test_recorded_id_of_someone_else_is_never_terminated(machines):
    fake = _FakeEC2WithSG()
    machines("bob-laptop", BOB)
    _aws(fake).provision(_spec(), _provider())
    bobs = _only_live_id(fake, BOB)
    machines("alice-laptop", ALICE)
    _aws(fake).deprovision(_spec(known_instance_ids=[bobs]), _provider())
    assert bobs not in fake.terminated


# -- tags on every billable resource -------------------------------------------------


def test_volume_and_eni_get_the_same_tags_as_the_instance(machines):
    fake = _FakeEC2WithSG()
    captured = {}
    real = fake.run_instances

    def spy(**kw):
        if not kw.get("DryRun"):
            captured.update(kw)
        return real(**kw)

    fake.run_instances = spy
    machines("alice-laptop", ALICE)
    _aws(fake).provision(_spec(), _provider(volume_size_gib=200))
    specs = captured["TagSpecifications"]
    assert [s["ResourceType"] for s in specs] == ["instance", "volume", "network-interface"]
    assert specs[1]["Tags"] == specs[0]["Tags"] == specs[2]["Tags"]
    tags = {t["Key"]: t["Value"] for t in specs[0]["Tags"]}
    key = load_lab_ssh_key("rancher-test")
    assert tags["rodeo-owner"] == tags["rodeo-created-by"] == ALICE
    assert tags["rodeo-deployment"] == key.deployment
    assert tags["rodeo-version"]
    sg = next(g for g in fake.security_groups.values() if g["GroupName"].startswith("rodeo-rancher"))
    assert sg["GroupName"] == f"rodeo-rancher-test-{key.deployment}"


def test_reserved_label_fails_before_run_instances(machines):
    fake = _FakeEC2WithSG()
    calls = []
    fake.run_instances = lambda **kw: calls.append(kw)
    machines("alice-laptop", ALICE)
    for bad in ({"rodeo-workshop": "x"}, {"ManagedBy": "me"}, {"managed-by": "me"},
                {"rodeo-owner": BOB}):
        with pytest.raises(ConfigError, match="reserved"):
            _aws(fake).provision(_spec(), _provider(labels=bad))
    assert calls == []


def test_security_group_with_our_name_but_other_owner_is_not_reused(machines):
    fake = _FakeEC2WithSG()
    machines("alice-laptop", ALICE)
    key = new_lab_ssh_key("rancher-test", region="eu-central-1")
    fake.security_groups["sg-squat"] = {
        "GroupId": "sg-squat", "GroupName": f"rodeo-rancher-test-{key.deployment}",
        "VpcId": "vpc-1", "IpPermissions": [],
        "Tags": [{"Key": "ManagedBy", "Value": "rodeo"}, {"Key": "rodeo-owner", "Value": BOB}],
    }
    p = _aws(fake)
    with pytest.raises(ConfigError, match="will not reuse"):
        p._resolve_security_groups(fake, _provider(), workshop="rancher-test",
                                   identity=p.identity_tags(_provider(), key))


# -- labs from before this change ------------------------------------------------------


def _legacy_instance(fake, key_name, *, expires="2099-01-01T00:00:00Z", iid="i-legacy"):
    fake.instances[iid] = {
        "InstanceId": iid, "State": {"Name": "running"}, "PublicIpAddress": "203.0.113.50",
        "KeyName": key_name,
        "Tags": [{"Key": "ManagedBy", "Value": "rodeo"},
                 {"Key": "rodeo-workshop", "Value": "rancher-test"},
                 {"Key": "rodeo-host-id", "Value": "primary"},
                 {"Key": "rodeo-expires-at", "Value": expires}],
    }
    return iid


@pytest.mark.parametrize("expires", ["2099-01-01T00:00:00Z", "never"])
def test_legacy_lab_with_the_local_key_is_adopted_and_reused(machines, expires):
    fake = _FakeEC2WithSG()
    machines("alice-laptop", ALICE)
    key = new_lab_ssh_key("rancher-test", region="eu-central-1")
    iid = _legacy_instance(fake, key.key_name, expires=expires)
    hosts = _aws(fake).provision(_spec(), _provider())
    assert hosts[0].provider_id == iid and hosts[0].labels["provision_action"] == "reuse"
    tags = _tags(fake.instances[iid])
    assert tags["rodeo-owner"] == ALICE and tags["rodeo-deployment"] == key.deployment
    assert tags["rodeo-expires-at"] == expires


def test_legacy_lab_without_the_local_key_is_left_alone(machines):
    fake = _FakeEC2WithSG()
    machines("alice-laptop", ALICE)
    iid = _legacy_instance(fake, "rodeo-rancher-test-deadbeef")
    _aws(fake).provision(_spec(), _provider())
    assert "rodeo-deployment" not in _tags(fake.instances[iid])
    assert len(_live(fake)) == 2  # ours is a new deployment
    _aws(fake).deprovision(_spec(), _provider())
    assert iid not in fake.terminated


def test_legacy_destroy_with_the_key_skips_a_new_neighbour(machines):
    fake = _FakeEC2WithSG()
    machines("bob-laptop", BOB)
    _aws(fake).provision(_spec(), _provider())
    bobs = _only_live_id(fake, BOB)
    machines("alice-laptop", ALICE)
    key = new_lab_ssh_key("rancher-test", region="eu-central-1")
    mine = _legacy_instance(fake, key.key_name)
    _aws(fake).deprovision(_spec(), _provider())
    assert mine in fake.terminated and bobs not in fake.terminated


# -- owner identity ---------------------------------------------------------------------


def test_rodeo_owner_env_skips_sts(monkeypatch):
    monkeypatch.setenv("RODEO_OWNER", "ci-runner")
    assert caller_owner("eu-north-1", sts_fn=lambda r: pytest.fail("STS called")) == "ci-runner"


def test_owner_is_the_full_caller_arn_without_env(monkeypatch):
    monkeypatch.delenv("RODEO_OWNER", raising=False)
    assert caller_owner("eu-north-1", sts_fn=lambda r: ALICE) == ALICE


def test_owner_over_the_tag_limit_is_refused_not_truncated():
    with pytest.raises(ConfigError, match="256"):
        check_owner("arn:" + "x" * 300)


def test_deployment_id_is_stored_and_old_keys_fall_back_to_the_name(machines):
    machines("alice-laptop", ALICE)
    key = new_lab_ssh_key("lab", region="eu-central-1")
    assert (lab_ssh_dir("lab") / "deployment_id").read_text().strip() == key.deployment
    assert len(key.deployment) == 8
    (lab_ssh_dir("lab") / "deployment_id").unlink()  # a key made before #175
    assert load_lab_ssh_key("lab").deployment == key.deployment


# -- rodeo cloud ----------------------------------------------------------------------------


def _cli(fake, monkeypatch, *args, input=None):
    from rodeo.cli import cli

    monkeypatch.setattr("rodeo.commands.cloud_cmd.AwsHostProvider", lambda: _aws(fake))
    return CliRunner().invoke(cli, ["cloud", *args, "--region", "eu-central-1"], input=input)


def test_cloud_ls_mine_and_all(machines, monkeypatch):
    fake = _FakeEC2WithSG()
    machines("alice-laptop", ALICE)
    _aws(fake).provision(_spec(), _provider())
    machines("bob-laptop", BOB)
    _aws(fake).provision(_spec(), _provider())
    bob_dep = load_lab_ssh_key("rancher-test").deployment
    machines("alice-laptop", ALICE)
    alice_dep = load_lab_ssh_key("rancher-test").deployment
    mine = _cli(fake, monkeypatch, "ls")
    assert mine.exit_code == 0, mine.output
    assert alice_dep in mine.output and bob_dep not in mine.output
    everyone = _cli(fake, monkeypatch, "ls", "--all")
    assert alice_dep in everyone.output and bob_dep in everyone.output


def test_cloud_destroy_works_after_the_local_key_is_gone(machines, monkeypatch):
    fake = _FakeEC2WithSG()
    machines("alice-laptop", ALICE)
    _aws(fake).provision(_spec(), _provider())
    key = load_lab_ssh_key("rancher-test")
    iid = _only_live_id(fake, ALICE)
    machines("alice-other-laptop", ALICE)  # same person, nothing local
    assert key.deployment in _cli(fake, monkeypatch, "ls").output
    out = _cli(fake, monkeypatch, "destroy", key.deployment, "--yes")
    assert out.exit_code == 0, out.output
    assert iid in fake.terminated
    assert not any(_tags(g).get("rodeo-deployment") == key.deployment
                   for g in fake.security_groups.values())
    assert key.key_name not in fake.key_pairs


def test_cloud_destroy_someone_elses_needs_not_mine_and_the_typed_owner(machines, monkeypatch):
    fake = _FakeEC2WithSG()
    machines("bob-laptop", BOB)
    _aws(fake).provision(_spec(), _provider())
    dep = load_lab_ssh_key("rancher-test").deployment
    bobs = _only_live_id(fake, BOB)
    machines("alice-laptop", ALICE)

    refused = _cli(fake, monkeypatch, "destroy", dep, "--yes")
    assert refused.exit_code != 0 and "--not-mine" in refused.output
    empty = _cli(fake, monkeypatch, "destroy", dep, "--not-mine", input="\n")
    wrong = _cli(fake, monkeypatch, "destroy", dep, "--not-mine", input="bob\n")
    assert empty.exit_code != 0 and wrong.exit_code != 0
    assert fake.terminated == []

    ok = _cli(fake, monkeypatch, "destroy", dep, "--not-mine", input=BOB + "\n")
    assert ok.exit_code == 0, ok.output
    assert fake.terminated == [bobs]
    machines("bob-laptop", BOB)
    assert load_lab_ssh_key("rancher-test") is not None  # Bob's local files untouched
