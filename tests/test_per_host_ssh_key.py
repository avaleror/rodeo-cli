"""provider.per_host_ssh_key: each lab host gets its own root key (opt-in)."""
from __future__ import annotations

from pathlib import Path

import pytest

import rodeo
from rodeo import ssh_key as sk
from rodeo.fleet.inventory import FleetHost, FleetInventory
from rodeo.fleet.ssh_exec import RemoteResult


@pytest.fixture
def lab_key(tmp_path, monkeypatch):
    monkeypatch.setattr(sk, "rodeo_ssh_dir", lambda: tmp_path / "ssh")
    monkeypatch.setattr(sk, "fix_invoking_ownership", lambda: None)
    return sk.new_lab_ssh_key("ws", region="eu-north-1")


def test_off_by_default_plants_the_lab_key(lab_key):
    assert not sk.per_host_ssh_key({})
    assert sk.planted_key({"type": "aws"}, lab_key, "lab-1") == lab_key


def test_on_gives_each_host_its_own_stable_key(lab_key):
    cfg = {"per_host_ssh_key": True}
    a, b = sk.planted_key(cfg, lab_key, "lab-1"), sk.planted_key(cfg, lab_key, "lab-2")
    assert a.private.read_text() != b.private.read_text() != lab_key.private.read_text()
    assert a.private.parent.parent == lab_key.private.parent / "hosts"
    assert oct(a.private.stat().st_mode & 0o777) == "0o600"
    assert sk.planted_key(cfg, lab_key, "lab-1").private.read_text() == a.private.read_text()
    # Same deployment and EC2 key pair: nothing new registered in the cloud.
    assert (a.key_name, a.deployment) == (lab_key.key_name, lab_key.deployment)


def test_deleting_the_lab_keys_deletes_the_host_keys(lab_key):
    key = sk.planted_key({"per_host_ssh_key": True}, lab_key, "lab-1")
    sk.delete_lab_ssh_key("ws")
    assert not key.private.exists()


def test_plant_sends_the_host_key_on_stdin(lab_key, monkeypatch):
    sent = []

    def fake(inventory, host, argv, *, timeout=120.0, as_root=True, stdin=None):
        sent.append(stdin)
        return RemoteResult(host.id, 0, "", "")

    monkeypatch.setattr(sk, "run_remote", fake)
    inv = FleetInventory(name="ws", lab_dir="/", hosts=[], defaults={})
    for hid in ("lab-1", "lab-2"):
        sk.plant_rodeo_ssh_key(inv, FleetHost(id=hid, ssh="10.0.0.1"),
                               key=sk.planted_key({"per_host_ssh_key": True}, lab_key, hid))
    privates = [s for s in sent if s]
    assert len(privates) == 2 and privates[0] != privates[1]
    assert lab_key.private.read_text() not in privates


@pytest.mark.parametrize("provider", ["aws", "gcp"])
def test_both_providers_plant_through_planted_key(provider):
    src = (Path(rodeo.__file__).parent / "providers" / f"{provider}.py").read_text()
    assert "planted_key(cfg, lab_key, host.id)" in src
    assert "key=lab_key)" not in src
