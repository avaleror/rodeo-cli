"""rodeo fleet player-setup and the player port in open-access (on by default)."""
from __future__ import annotations

import json

import pytest
import yaml

from rodeo.config import ConfigError
from rodeo.fleet import player as fpl
from rodeo.fleet.inventory import PLAYER_PORT, load_inventory
from rodeo.fleet.ssh_exec import RemoteResult
from rodeo.fleet.student_access import player_ports

SHA = "77022fd51ab5797cd201b9512fe44af3dc2ae9f6"
LINK = "https://1-2-3-4.sslip.io/enter?t=SECRETSECRETSECRETSECRET"


def _inv(tmp_path, *, player: bool, hosts=2, profile="virt-rodeo"):
    data = {
        "name": "ws", "lab": {"dir": "/root/lab", "profile": profile},
        "provider": {"type": "aws", "region": "eu-north-1", "subnet_id": "subnet-1",
                     "instance_type": "m8id.8xlarge", "student_access": "open"},
        "portal": {"enabled": True},
        "hosts": [{"id": f"lab-{i}", "ssh": f"10.0.0.{i}", "public_ip": f"10.0.0.{i}"} for i in range(1, hosts + 1)],
    }
    if not player:
        data["player"] = False
    p = tmp_path / "workshop.yaml"
    p.write_text(yaml.safe_dump(data))
    return load_inventory(p)


def test_player_port_unless_the_fleet_opts_out(tmp_path):
    assert player_ports(_inv(tmp_path, player=False)) == ()
    assert player_ports(_inv(tmp_path, player=True)) == (PLAYER_PORT,) == (443,)


def test_aws_allows_the_player_port_but_still_never_22():
    from rodeo.providers import aws

    src = open(aws.__file__).read()
    assert "(*MANAGED_SG_PORTS, STUDENT_SSH_PORT, PLAYER_PORT)" in src  # 2222 kept only to close it
    assert 22 in aws.MANAGED_SG_PORTS and 443 not in aws.MANAGED_SG_PORTS


def test_player_setup_runs_on_every_host_and_never_returns_the_link(tmp_path, monkeypatch):
    inv = _inv(tmp_path, player=True)
    seen = []

    def fake_remote(inventory, host, argv, *, timeout=120.0, as_root=True, stdin=None):
        seen.append((host.id, argv))
        out = json.dumps({"access_url": LINK, "player": f"https://{host.id}.example/", "chapters": 8})
        return RemoteResult(host.id, 0, out + "\n", "")

    monkeypatch.setattr(fpl, "run_remote", fake_remote)
    rows = fpl.fleet_player_setup(inv, track_ref=SHA)
    assert [r.id for r in rows] == ["lab-1", "lab-2"] and all(r.ok and r.chapters == 8 for r in rows)
    assert all(argv == ["rodeo", "player", "setup", "--json", "--track-ref", SHA] for _, argv in seen)
    assert "SECRET" not in json.dumps([r.__dict__ for r in rows])


def test_player_setup_reports_failures(tmp_path, monkeypatch):
    inv = _inv(tmp_path, player=True, hosts=1)
    monkeypatch.setattr(fpl, "run_remote",
                        lambda *a, **k: RemoteResult("lab-1", 1, "", "harvester-kubeconfig missing"))
    row = fpl.fleet_player_setup(inv, track_ref=SHA)[0]
    assert not row.ok and "kubeconfig" in row.error


@pytest.mark.parametrize("ref", [None, "main"])
def test_player_setup_with_a_track_needs_a_pinned_sha(tmp_path, ref):
    with pytest.raises(ConfigError, match="--track-ref"):
        fpl.fleet_player_setup(_inv(tmp_path, player=True), track_ref=ref)


def test_player_setup_without_a_track_needs_no_ref_and_names_the_profile(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(fpl, "run_remote", lambda inventory, host, argv, **k: seen.append(argv) or RemoteResult(
        host.id, 0, json.dumps({"player": "https://x/", "chapters": 0}) + "\n", ""))
    rows = fpl.fleet_player_setup(_inv(tmp_path, player=True, hosts=1, profile="virt-workshop"))
    assert rows[0].ok and rows[0].chapters == 0
    assert seen == [["rodeo", "player", "setup", "--json", "--profile", "virt-workshop"]]


def test_player_setup_refuses_a_profile_without_player_yaml(tmp_path):
    with pytest.raises(ConfigError, match="no player.yaml"):
        fpl.fleet_player_setup(_inv(tmp_path, player=True, profile="smlm-workshop"))


def test_open_access_with_the_player_opens_443_only(tmp_path):
    from rodeo.fleet.student_access import open_access_ports
    assert open_access_ports(_inv(tmp_path, player=True)) == (443,)


def test_open_access_without_the_player_opens_the_ui_ports_never_ssh(tmp_path):
    from rodeo.fleet.student_access import open_access_ports
    ports = open_access_ports(_inv(tmp_path, player=False))
    assert ports == (8443, 30002) and 22 not in ports and 2222 not in ports
