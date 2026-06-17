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


def test_instruqt_shows_tab_hint(capsys):
    cfg = {
        "deployment_target": "instruqt",
        "network": {"vip": "192.168.122.10", "rancher_ip": "192.168.122.9"},
        "vms": {
            "harvester1": {"ip": "192.168.122.11"},
            "rancher": {"ip": "192.168.122.9"},
        },
    }
    out = _render(cfg, capsys)
    assert "Instruqt lab UI" in out
    assert "track config" in out
    assert "192.168.122.10" in out
    assert ":8443" not in out


def test_baremetal_shows_dnat_url(capsys, monkeypatch):
    monkeypatch.setattr(success, "_host_ip", lambda: "10.1.2.3")
    cfg = {
        "deployment_target": "baremetal",
        "network": {"vip": "192.168.122.10", "rancher_ip": "192.168.122.9"},
        "vms": {
            "harvester1": {"ip": "192.168.122.11"},
            "rancher": {"ip": "192.168.122.9"},
        },
    }
    out = _render(cfg, capsys)
    # VIP is the canonical Harvester URL; DNAT info is shown as a note.
    assert "https://192.168.122.10" in out
    assert "10.1.2.3:8443" in out   # DNAT note still present
    assert "10.1.2.3:30002" in out
    assert "Instruqt" not in out
