"""Topology-aware success screen."""
from __future__ import annotations

from rodeo import success


def _render(cfg, capsys):
    success.render_success(cfg)
    return capsys.readouterr().out


def test_rancher_only_hides_harvester(capsys):
    cfg = {"network": {"rancher_ip": "192.168.122.9"}, "vms": {"rancher": {"ip": "192.168.122.9"}}}
    out = _render(cfg, capsys)
    assert "Rancher Prime" in out
    assert "Harvester UI" not in out
    assert "ssh harvester1" not in out
    assert "ssh rancher" in out


def test_harvester_shows_both(capsys):
    cfg = {
        "network": {"vip": "192.168.122.10", "rancher_ip": "192.168.122.9"},
        "vms": {
            "harvester1": {"ip": "192.168.122.11"},
            "rancher": {"ip": "192.168.122.9"},
        },
    }
    out = _render(cfg, capsys)
    assert "Harvester UI" in out
    assert "Rancher Prime" in out
    assert "ssh harvester1" in out
