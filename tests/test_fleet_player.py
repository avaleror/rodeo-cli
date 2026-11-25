"""rodeo fleet player-setup and the player port in open-access (opt-in)."""
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


def _inv(tmp_path, *, player: bool, hosts=2):
    data = {
        "name": "ws", "lab": {"dir": "/root/lab", "profile": "virt-rodeo"},
        "provider": {"type": "aws", "region": "eu-north-1", "subnet_id": "subnet-1",
                     "instance_type": "m8id.8xlarge", "student_access": "open"},
        "portal": {"enabled": True, "player": player},
        "hosts": [{"id": f"lab-{i}", "ssh": f"10.0.0.{i}", "public_ip": f"10.0.0.{i}"} for i in range(1, hosts + 1)],
    }
    p = tmp_path / "workshop.yaml"
    p.write_text(yaml.safe_dump(data))
    return load_inventory(p)


def test_player_port_only_with_the_flag(tmp_path):
    assert player_ports(_inv(tmp_path, player=False)) == ()
    assert player_ports(_inv(tmp_path, player=True)) == (PLAYER_PORT,) == (443,)


def test_aws_allows_the_player_port_but_still_never_22():
    from rodeo.providers import aws

    src = open(aws.__file__).read()
    assert "(*MANAGED_SG_PORTS, STUDENT_SSH_PORT, PLAYER_PORT)" in src
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
    assert all(argv == ["rodeo", "player", "setup", "--track-ref", SHA, "--json"] for _, argv in seen)
    assert "SECRET" not in json.dumps([r.__dict__ for r in rows])


def test_player_setup_reports_failures(tmp_path, monkeypatch):
    inv = _inv(tmp_path, player=True, hosts=1)
    monkeypatch.setattr(fpl, "run_remote",
                        lambda *a, **k: RemoteResult("lab-1", 1, "", "harvester-kubeconfig missing"))
    row = fpl.fleet_player_setup(inv, track_ref=SHA)[0]
    assert not row.ok and "kubeconfig" in row.error


def test_player_setup_needs_a_pinned_sha(tmp_path):
    with pytest.raises(ConfigError, match="40-character"):
        fpl.fleet_player_setup(_inv(tmp_path, player=True), track_ref="main")
