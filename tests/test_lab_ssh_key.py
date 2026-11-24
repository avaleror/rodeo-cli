"""Per-lab SSH keys on AWS: every deployment generates its own key pair, uses
it for that lab only, and deletes it (locally and in EC2) with the lab."""
from __future__ import annotations

import base64

import pytest

from rodeo.fleet.inventory import FleetInventory
from rodeo.fleet.ssh_exec import _identity
from rodeo.providers.aws import AwsHostProvider
from rodeo.providers.base import ProvisionSpec
from rodeo.ssh_key import (
    DEFAULT_EC2_KEY_NAME,
    delete_ec2_key_pair,
    lab_ssh_dir,
    load_lab_ssh_key,
    new_lab_ssh_key,
    resolve_ssh_identity,
    rodeo_ssh_public_key_path,
)
from tests.conftest import TEST_OWNER
from tests.test_aws_managed_sg import _FakeEC2WithSG, _provider
from tests.test_aws_managed_sg import managed_ssh  # noqa: F401 (fixture)


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr("rodeo.providers.aws.AwsHostProvider._wait_ssh", lambda *a, **k: None)


def _aws(fake):
    return AwsHostProvider(ec2_client=fake, sleep=lambda s: None, caller_ip_fn=lambda: "203.0.113.9")


def _spec(workshop="demo", **kw):
    return ProvisionSpec(workshop=workshop, host_ids=["primary"], ssh_user="ec2-user",
                         wait_ssh=kw.pop("wait_ssh", False), **kw)


def test_new_lab_key_is_fresh_every_time(managed_ssh):  # noqa: F811
    a = new_lab_ssh_key("demo", region="eu-north-1")
    a_pub = a.public.read_text()
    b = new_lab_ssh_key("demo", region="eu-north-1")
    assert a.key_name != b.key_name
    assert a_pub != b.public.read_text()
    assert b.key_name.startswith("rodeo-demo-")
    assert load_lab_ssh_key("demo") == b
    assert b.public.read_text() != rodeo_ssh_public_key_path().read_text()
    assert oct(b.private.stat().st_mode & 0o777) == "0o600"


def test_delete_ec2_key_pair_never_touches_the_shared_pair():
    class Boom:
        def delete_key_pair(self, KeyName):
            raise AssertionError("must not be called")

    assert delete_ec2_key_pair(Boom(), DEFAULT_EC2_KEY_NAME) is False


def test_provision_uses_a_new_lab_key_not_the_machine_key(managed_ssh, no_wait, monkeypatch):  # noqa: F811
    fake = _FakeEC2WithSG()
    captured = {}
    real = fake.run_instances

    def spy(**kw):
        if not kw.get("DryRun"):
            captured.update(kw)
        return real(**kw)

    fake.run_instances = spy
    _aws(fake).provision(_spec(), _provider())
    key = load_lab_ssh_key("demo")
    assert key is not None and key.region == "eu-central-1"
    assert captured["KeyName"] == key.key_name
    assert fake.imported == [key.key_name]
    tags = {t["Key"]: t["Value"] for t in fake.key_pairs[key.key_name]["Tags"]}
    assert tags == {
        "ManagedBy": "rodeo", "rodeo-workshop": "demo",
        "rodeo-owner": TEST_OWNER, "rodeo-deployment": key.deployment,
        "rodeo-version": tags["rodeo-version"], "rodeo-created-by": TEST_OWNER,
    }
    assert key.key_name.endswith("-" + key.deployment)
    lab_pub = key.public.read_text().split()[1]
    machine_pub = rodeo_ssh_public_key_path().read_text().split()[1]
    assert lab_pub in captured["UserData"]
    assert machine_pub not in captured["UserData"]
    # Everything that SSHes to this lab afterwards picks the lab key.
    assert resolve_ssh_identity(workshop="demo") == str(key.private)
    inv = FleetInventory(name="demo", lab_dir="/", defaults={}, hosts=[])
    assert _identity(inv) == str(key.private)


def test_plant_gets_the_lab_key(managed_ssh, monkeypatch):  # noqa: F811
    monkeypatch.setattr("rodeo.providers.aws.AwsHostProvider._wait_ssh", lambda *a, **k: None)
    planted = []
    monkeypatch.setattr("rodeo.providers.aws.plant_rodeo_ssh_key",
                        lambda inv, fh, key=None: planted.append((inv.identity_file, key)))
    fake = _FakeEC2WithSG()
    _aws(fake).provision(_spec(wait_ssh=True), _provider())
    key = load_lab_ssh_key("demo")
    assert planted == [(str(key.private), key)]


def test_reuse_keeps_the_key_and_destroy_deletes_it(managed_ssh, no_wait):  # noqa: F811
    fake = _FakeEC2WithSG()
    p = _aws(fake)
    p.provision(_spec(), _provider())
    first = load_lab_ssh_key("demo")
    p.provision(_spec(), _provider())  # re-run of `rodeo up` on a live lab
    assert load_lab_ssh_key("demo") == first
    assert fake.imported == [first.key_name]

    results = p.deprovision(_spec(), _provider())
    assert any(r.id == "key-pair" and r.detail == "deleted" for r in results)
    assert first.key_name not in fake.key_pairs
    assert not lab_ssh_dir("demo").exists()

    p.provision(_spec(), _provider())  # next deployment: brand new key
    second = load_lab_ssh_key("demo")
    assert second.key_name != first.key_name


def test_partial_fleet_teardown_keeps_the_key(managed_ssh, no_wait):  # noqa: F811
    fake = _FakeEC2WithSG()
    p = _aws(fake)
    spec = ProvisionSpec(workshop="ws", host_ids=["s1", "s2"], ssh_user="root", wait_ssh=False)
    p.provision(spec, _provider())
    key = load_lab_ssh_key("ws")
    one = ProvisionSpec(workshop="ws", host_ids=["s1"], ssh_user="root", wait_ssh=False)
    results = p.deprovision(one, _provider())
    assert not any(r.id == "key-pair" for r in results)
    assert load_lab_ssh_key("ws") == key


def test_lab_whose_instances_vanished_gets_a_new_key(managed_ssh, no_wait):  # noqa: F811
    """Dead-man switch terminated the host: the old pair is deleted, a new one made."""
    fake = _FakeEC2WithSG()
    p = _aws(fake)
    p.provision(_spec(), _provider())
    old = load_lab_ssh_key("demo")
    for inst in fake.instances.values():
        inst["State"] = {"Name": "terminated"}
    p.provision(_spec(), _provider())
    assert old.key_name in fake.deleted_key_pairs
    assert load_lab_ssh_key("demo").key_name != old.key_name


def test_stale_keys_of_other_labs_are_swept(managed_ssh, no_wait):  # noqa: F811
    fake = _FakeEC2WithSG()
    p = _aws(fake)
    p.provision(_spec("gone"), _provider())
    gone = load_lab_ssh_key("gone")
    other_region = new_lab_ssh_key("elsewhere", region="us-east-1")
    for inst in fake.instances.values():
        inst["State"] = {"Name": "terminated"}  # dead-man switch
    p.provision(_spec("demo"), _provider())
    assert gone.key_name in fake.deleted_key_pairs
    assert load_lab_ssh_key("gone") is None
    # A key recorded for another region is not this client's to judge.
    assert load_lab_ssh_key("elsewhere") == other_region


def _live(fake):
    return [i for i in fake.instances.values() if i["State"]["Name"] == "running"]


def test_someone_elses_lab_with_the_same_name_is_left_alone(managed_ssh, no_wait):  # noqa: F811
    """Shared account (issue #175): another machine's live `rancher` lab is
    neither adopted nor a blocker. This machine gets its own deployment."""
    fake = _FakeEC2WithSG()
    _aws(fake).provision(_spec("rancher"), _provider())
    theirs = _live(fake)[0]["InstanceId"]
    # Same lab name, other machine: no local key for it.
    import shutil

    shutil.rmtree(lab_ssh_dir("rancher"))
    _aws(fake).provision(_spec("rancher"), _provider())
    live = _live(fake)
    assert len(live) == 2 and theirs in {i["InstanceId"] for i in live}
    deps = {t["Value"] for i in live for t in i["Tags"] if t["Key"] == "rodeo-deployment"}
    assert len(deps) == 2


def test_local_key_that_does_not_match_the_live_instance_starts_a_new_deployment(
    managed_ssh, no_wait,  # noqa: F811
):
    fake = _FakeEC2WithSG()
    _aws(fake).provision(_spec("rancher"), _provider())
    other = _live(fake)[0]["InstanceId"]
    new_lab_ssh_key("rancher", region="eu-central-1")  # stale/foreign local key
    _aws(fake).provision(_spec("rancher"), _provider())
    assert other in {i["InstanceId"] for i in _live(fake)}
    assert other not in fake.terminated


def test_fake_fingerprint_matches_ssh_keygen(managed_ssh):  # noqa: F811
    """Guards the EC2 fake: its fingerprint must equal what ensure_ec2_key_pair compares."""
    from rodeo.ssh_key import local_pubkey_sha256

    key = new_lab_ssh_key("demo")
    blob = base64.b64decode(key.public.read_text().split()[1])
    import hashlib

    ec2_style = base64.b64encode(hashlib.sha256(blob).digest()).decode()
    assert local_pubkey_sha256(key.public).removeprefix("SHA256:") == ec2_style.rstrip("=")
