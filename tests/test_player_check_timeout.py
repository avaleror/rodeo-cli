"""How long one chapter check may run: 120 s by default, set in the player config."""
from __future__ import annotations

import json

import pytest
import yaml

from rodeo.config import ConfigError
from rodeo.fleet import player as fpl
from rodeo.fleet.inventory import load_inventory
from rodeo.player import setup as s
from rodeo.player.runner import CHECK_TIMEOUT, check_timeout_ok
from rodeo.player.server import load_config

SHA = "77022fd51ab5797cd201b9512fe44af3dc2ae9f6"


def _cfg(tmp_path, **extra):
    p = tmp_path / "player.json"
    p.write_text(json.dumps({"track_dir": str(tmp_path), "state_dir": str(tmp_path / "s"), **extra}))
    return p


def test_default_is_120_seconds(tmp_path):
    assert CHECK_TIMEOUT == 120
    assert load_config(_cfg(tmp_path)).check_timeout == 120


def test_player_config_sets_it(tmp_path):
    assert load_config(_cfg(tmp_path, check_timeout=600)).check_timeout == 600


@pytest.mark.parametrize("bad", [0, 9, 3601, -5, 1.5, "120", True, None])
def test_out_of_range_or_not_whole_seconds_is_refused(tmp_path, bad):
    with pytest.raises(ValueError, match="check_timeout"):
        load_config(_cfg(tmp_path, check_timeout=bad))


def test_limits_are_accepted():
    assert check_timeout_ok(10) == 10 and check_timeout_ok(3600) == 3600


def test_setup_writes_it_into_the_player_config():
    opts = s.SetupOptions(base="13-61-184-227.sslip.io", track_ref=SHA)
    assert s.player_config(opts)["check_timeout"] == 120
    opts = s.SetupOptions(base="13-61-184-227.sslip.io", track_ref=SHA, check_timeout=300)
    opts.validate()
    assert s.player_config(opts)["check_timeout"] == 300
    with pytest.raises(ValueError, match="check_timeout"):
        s.SetupOptions(base="13-61-184-227.sslip.io", track_ref=SHA, check_timeout=5).validate()


def _inv(tmp_path):
    data = {
        "name": "ws", "lab": {"dir": "/root/lab", "profile": "virt-rodeo"},
        "provider": {"type": "aws", "region": "eu-north-1", "subnet_id": "subnet-1",
                     "instance_type": "m8id.8xlarge", "student_access": "open"},
        "portal": {"enabled": True, "player": True},
        "hosts": [{"id": "lab-1", "ssh": "10.0.0.1", "public_ip": "10.0.0.1"}],
    }
    p = tmp_path / "workshop.yaml"
    p.write_text(yaml.safe_dump(data))
    return load_inventory(p)


def test_fleet_passes_it_to_every_lab_only_when_set(tmp_path):
    inv = _inv(tmp_path)
    host = inv.hosts[0]
    assert "--check-timeout" not in fpl.setup_argv(inv, host, SHA)
    argv = fpl.setup_argv(inv, host, SHA, 300)
    assert argv[argv.index("--check-timeout") + 1] == "300"


def test_fleet_refuses_a_bad_value_before_touching_any_lab(tmp_path):
    with pytest.raises(ConfigError, match="check_timeout"):
        fpl.fleet_player_setup(_inv(tmp_path), track_ref=SHA, check_timeout=5)
