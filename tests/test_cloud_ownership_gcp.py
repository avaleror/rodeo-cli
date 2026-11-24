"""Issue #175 on GCP: the same ownership rule as AWS.

Labels cannot hold '@' or '.', so rodeo-owner is a readable label plus a hash of
the raw account, and the raw account travels in instance metadata. Firewall
rules take no labels: their description records owner and deployment.
"""
from __future__ import annotations

import re
import shutil

import pytest
from click.testing import CliRunner

from rodeo.config import ConfigError
from rodeo.providers import gcp
from rodeo.providers.base import ProvisionSpec
from rodeo.providers.gcp import GcpHostProvider, caller_owner, owner_label
from rodeo.ssh_key import lab_ssh_dir, load_lab_ssh_key, new_lab_ssh_key
from tests.test_providers_gcp import FakeApi

ALICE = "alice@example.com"
BOB = "bob@example.com"
CFG = {"type": "gcp", "instance_type": "c4-standard-8-lssd", "region": "europe-west4"}


@pytest.fixture
def machines(tmp_path, monkeypatch):
    """Switch between two laptops: each has its own ~/.rodeo/ssh and owner."""
    def use(name: str, owner: str) -> None:
        d = tmp_path / name / "ssh"
        monkeypatch.setattr("rodeo.paths.rodeo_ssh_dir", lambda: d)
        monkeypatch.setattr("rodeo.ssh_key.rodeo_ssh_dir", lambda: d)
        monkeypatch.setenv("RODEO_OWNER", owner)

    return use


def _p(api) -> GcpHostProvider:
    return GcpHostProvider(api=api, sleep=lambda _s: None, caller_ip_fn=lambda: "198.51.100.9")


def _spec(workshop="rancher-test", **kw) -> ProvisionSpec:
    return ProvisionSpec(workshop=workshop, host_ids=["primary"], ssh_user="rodeo",
                         wait_ssh=False, **kw)


def _of(api, owner):
    return [n for n, i in api.instances.items() if i["metadata"].get("rodeo-owner") == owner]


def test_same_lab_name_two_owners_destroy_only_removes_your_own(machines):
    api = FakeApi()
    machines("alice-laptop", ALICE)
    _p(api).provision(_spec(), CFG)
    machines("bob-laptop", BOB)
    _p(api).provision(_spec(), CFG)
    alice, bob = _of(api, ALICE), _of(api, BOB)
    assert len(alice) == len(bob) == 1 and alice != bob
    _p(api).deprovision(_spec(), CFG)
    assert api.deleted == bob
    assert len(api.firewalls) == 1  # Alice's rule stays
    assert ALICE in next(iter(api.firewalls.values()))["description"]


def test_destroy_without_key_or_recorded_names_refuses(machines):
    api = FakeApi()
    machines("alice-laptop", ALICE)
    _p(api).provision(_spec(), CFG)
    machines("bob-laptop", BOB)
    with pytest.raises(ConfigError, match="cloud ls"):
        _p(api).deprovision(_spec(), CFG)
    assert api.deleted == []


def test_recorded_name_of_someone_else_is_never_deleted(machines):
    api = FakeApi()
    machines("bob-laptop", BOB)
    _p(api).provision(_spec(), CFG)
    bobs = _of(api, BOB)
    machines("alice-laptop", ALICE)
    _p(api).deprovision(_spec(known_instance_ids=bobs), CFG)
    assert api.deleted == []


def test_labels_metadata_and_disk_carry_the_owner_and_deployment(machines):
    api = FakeApi()
    machines("alice-laptop", ALICE)
    _p(api).provision(_spec(), CFG)
    key = load_lab_ssh_key("rancher-test")
    _, body = api.inserts[0]
    assert body["labels"]["rodeo-owner"] == owner_label(ALICE)
    assert body["labels"]["rodeo-deployment"] == key.deployment
    assert body["labels"]["rodeo-version"]
    disk = body["disks"][0]["initialize_params"]["labels"]
    assert disk["rodeo-owner"] == owner_label(ALICE) and disk["rodeo-deployment"] == key.deployment
    meta = {i["key"]: i["value"] for i in body["metadata"]["items"]}
    assert meta["rodeo-owner"] == meta["rodeo-created-by"] == ALICE
    assert body["name"] == f"rodeo-rancher-test-{key.deployment}-primary"
    assert body["tags"]["items"] == [f"rodeo-rancher-test-{key.deployment}"]


def test_owner_label_is_a_valid_label_and_keeps_lookalikes_apart():
    a, b = owner_label("a.b@x.com"), owner_label("a-b@x.com")
    assert a != b
    for value in (a, b, owner_label("x" * 200 + "@example.com")):
        assert re.fullmatch(r"[a-z0-9_-]{1,63}", value)


def test_long_names_keep_the_deployment_and_host():
    name = gcp.instance_name("w" * 80, "student-01", "0a1b2c3d")
    assert len(name) <= 63 and name.endswith("-0a1b2c3d-student-01")
    assert gcp.workshop_firewall("w" * 80, "0a1b2c3d").endswith("-0a1b2c3d")


def test_reserved_labels_fail_before_any_insert(machines):
    api = FakeApi()
    machines("alice-laptop", ALICE)
    with pytest.raises(ConfigError, match="reserved"):
        _p(api).provision(_spec(), {**CFG, "labels": {"rodeo-owner": "x"}})
    assert api.inserts == []


def test_firewall_of_another_owner_is_not_changed(machines):
    api = FakeApi()
    machines("alice-laptop", ALICE)
    key = new_lab_ssh_key("rancher-test")
    name = gcp.workshop_firewall("rancher-test", key.deployment)
    api.firewalls[name] = {"name": name, "source_ranges": ["192.0.2.1/32"],
                           "description": gcp._fw_description(BOB, key.deployment)}
    p = _p(api)
    p.owner()
    with pytest.raises(ConfigError, match="will not change"):
        p._reconcile_firewall(api, CFG, "net", name, key.deployment)
    assert api.firewalls[name]["source_ranges"] == ["192.0.2.1/32"]


# -- labs from before issue #175 ------------------------------------------------


def _legacy(api, key=None, name="rodeo-rancher-test-primary"):
    pub = key.public.read_text().strip() if key else "ssh-ed25519 AAAAsomeoneelse x"
    api.instances[name] = {
        "name": name, "zone": "europe-west4-a", "status": "RUNNING", "id": "9",
        "labels": {"managed-by": "rodeo", "rodeo-workshop": "rancher-test",
                   "rodeo-host-id": "primary", "rodeo-expires-at": "never"},
        "public_ip": "203.0.113.50", "tags": ["rodeo-rancher-test"],
        "metadata": {"ssh-keys": f"rodeo:{pub}", "rodeo-workshop": "rancher-test"},
    }
    api.firewalls["rodeo-rancher-test"] = {"name": "rodeo-rancher-test",
                                           "source_ranges": ["198.51.100.9/32"]}
    return name


def test_legacy_lab_with_the_local_key_is_adopted_and_keeps_its_firewall(machines):
    api = FakeApi()
    machines("alice-laptop", ALICE)
    key = new_lab_ssh_key("rancher-test")
    name = _legacy(api, key)
    hosts = _p(api).provision(_spec(), CFG)
    assert hosts[0].provider_id == name and hosts[0].labels["provision_action"] == "reuse"
    labels = api.instances[name]["labels"]
    assert labels["rodeo-deployment"] == key.deployment
    assert labels["rodeo-owner"] == owner_label(ALICE)
    assert set(api.firewalls) == {"rodeo-rancher-test"}  # no second rule
    _p(api).deprovision(_spec(), CFG)
    assert api.deleted == [name] and api.firewalls == {}


def test_legacy_lab_without_the_local_key_is_left_alone(machines):
    api = FakeApi()
    machines("alice-laptop", ALICE)
    name = _legacy(api)
    _p(api).provision(_spec(), CFG)
    assert "rodeo-deployment" not in api.instances[name]["labels"]
    _p(api).deprovision(_spec(), CFG)
    assert name not in api.deleted
    assert "rodeo-rancher-test" in api.firewalls  # still used by the legacy lab


# -- owner identity ------------------------------------------------------------------


def test_rodeo_owner_env_skips_the_account_lookup(monkeypatch):
    monkeypatch.setenv("RODEO_OWNER", "ci-runner")
    assert caller_owner(lambda: pytest.fail("looked up")) == "ci-runner"


def test_owner_is_the_adc_account_without_env(monkeypatch):
    monkeypatch.delenv("RODEO_OWNER", raising=False)
    assert caller_owner(lambda: ALICE) == ALICE


# -- rodeo cloud --provider gcp --------------------------------------------------------


def _cli(api, monkeypatch, *args, input=None):
    from rodeo.cli import cli

    monkeypatch.setattr("rodeo.providers.gcp.GcpHostProvider", lambda: _p(api))
    return CliRunner().invoke(cli, ["cloud", *args, "--provider", "gcp", "--project", "proj"],
                              input=input)


def test_cloud_ls_and_destroy_on_gcp_after_the_key_is_lost(machines, monkeypatch):
    api = FakeApi()
    machines("alice-laptop", ALICE)
    _p(api).provision(_spec(), CFG)
    dep = load_lab_ssh_key("rancher-test").deployment
    machines("bob-laptop", BOB)
    _p(api).provision(_spec(), CFG)
    bob_dep = load_lab_ssh_key("rancher-test").deployment
    machines("alice-other-laptop", ALICE)
    out = _cli(api, monkeypatch, "ls")
    assert out.exit_code == 0, out.output
    assert dep in out.output and bob_dep not in out.output
    refused = _cli(api, monkeypatch, "destroy", bob_dep, "--yes")
    assert refused.exit_code != 0 and api.deleted == []
    ok = _cli(api, monkeypatch, "destroy", dep, "--yes")
    assert ok.exit_code == 0, ok.output
    assert api.deleted == _of_deleted(dep)
    assert not [n for n in api.firewalls if n.endswith(dep)]


def _of_deleted(dep):
    return [f"rodeo-rancher-test-{dep}-primary"]


def test_cloud_destroy_gcp_not_mine_needs_the_typed_owner(machines, monkeypatch):
    api = FakeApi()
    machines("bob-laptop", BOB)
    _p(api).provision(_spec(), CFG)
    dep = load_lab_ssh_key("rancher-test").deployment
    machines("alice-laptop", ALICE)
    wrong = _cli(api, monkeypatch, "destroy", dep, "--not-mine", input="bob\n")
    assert wrong.exit_code != 0 and api.deleted == []
    ok = _cli(api, monkeypatch, "destroy", dep, "--not-mine", input=BOB + "\n")
    assert ok.exit_code == 0, ok.output
    assert api.deleted == _of_deleted(dep)
    machines("bob-laptop", BOB)
    assert lab_ssh_dir("rancher-test").exists()  # Bob's local files untouched
    shutil.rmtree(lab_ssh_dir("rancher-test"))
