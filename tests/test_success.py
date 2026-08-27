"""Topology-aware success screen."""
from __future__ import annotations

from rodeo import success
from rodeo.profiles import _REGISTRY
from rodeo.profiles.base import RodeoProfile


def _render(cfg, capsys):
    success.render_success(cfg)
    return capsys.readouterr().out


def test_suse_edge_sections_come_from_profile(capsys):
    cfg = {
        "type": "suse-edge",
        "network": {"rancher_ip": "192.168.122.9"},
        "vms": {
            "rancher": {"ip": "192.168.122.9"},
            "eib": {"ip": "192.168.122.20"},
            "edge1": {"ip": "192.168.122.31", "mac": "02:00:00:0E:62:A1"},
        },
    }
    out = _render(cfg, capsys)
    assert "Edge node reference" in out
    assert "rodeo ssh eib" in out
    assert "alien-geeko" in out


def test_custom_profile_owns_success_narrative(capsys, monkeypatch):
    class _MyLab(RodeoProfile):
        name = "my-lab"
        phases = []
        vm_names = []
        ansible_phases = frozenset()

        def default_cfg(self, config_dir=None):
            return {}

        def success_extra_sections(self, cfg):
            return ["[bold]Custom section[/bold]"]

        def success_next_steps(self, cfg):
            return ["  try the custom thing"]

    monkeypatch.setitem(_REGISTRY, "my-lab", _MyLab())
    cfg = {"type": "my-lab", "network": {}, "vms": {"vm1": {"ip": "10.0.0.1"}}}
    out = _render(cfg, capsys)
    assert "Custom section" in out
    assert "try the custom thing" in out


def test_unknown_type_falls_back_to_generic_next_steps(capsys):
    cfg = {"type": "no-such-lab", "network": {}, "vms": {"rancher": {"ip": "10.0.0.9"}}}
    out = _render(cfg, capsys)
    assert "ssh rancher" in out


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


def test_instruqt_shows_hostimage_checklist(capsys):
    cfg = {
        "deployment_target": "instruqt",
        "network": {"vip": "192.168.122.10", "rancher_ip": "192.168.122.9"},
        "vms": {
            "harvester1": {"ip": "192.168.122.11"},
            "rancher": {"ip": "192.168.122.9"},
        },
    }
    out = _render(cfg, capsys)
    assert "Instruqt hostimage checklist" in out
    assert "rodeo deploy --finalise" in out
    assert "15778/tcp" in out
    assert "15779/tcp" in out
    assert "rodeo start-if-needed" in out
    assert "Please Wait" in out


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
    assert "hostimage checklist" not in out
    assert "start-if-needed" not in out
