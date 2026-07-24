"""Tests for ``rodeo.service.status.status_report``."""
from __future__ import annotations

from dataclasses import dataclass

from rodeo.service.status import cacheable_phases_complete, status_report


@dataclass
class _FakeVM:
    name: str
    state: str
    autostart: bool


class _FakeLV:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def list_vms(self, names):
        return [
            _FakeVM(name="harvester1", state="running", autostart=True),
            _FakeVM(name="rancher", state="shut off", autostart=False),
        ]


def test_status_report_shape(monkeypatch):
    monkeypatch.setattr(
        "rodeo.service.status.load_state",
        lambda name: {
            "phases": {
                "kvm_host": {"completed": True, "timestamp": "2026-07-20T10:00:00+00:00"},
                "vms": {"completed": False, "last_error": "boom"},
            }
        },
    )
    monkeypatch.setattr("rodeo.service.status.vip_reachable", lambda vip: True)
    monkeypatch.setattr(
        "rodeo.engine.libvirt.LibvirtDriver",
        lambda uri: _FakeLV(),
    )

    cfg = {
        "name": "my-lab",
        "type": "suse-virt",
        "network": {"vip": "192.168.122.10"},
        "libvirt": {"uri": "qemu:///system"},
        "vms": {"harvester1": {}, "rancher": {}},
    }
    report = status_report(cfg)
    assert report["name"] == "my-lab"
    assert report["vip"] == "192.168.122.10"
    assert report["vip_reachable"] is True
    assert len(report["vms"]) == 2
    assert report["vms"][0]["name"] == "harvester1"
    assert report["vms"][0]["autostart"] is True
    assert report["phases"]["kvm_host"]["completed"] is True
    assert report["phases"]["vms"]["last_error"] == "boom"
    assert report["phases"]["apply"]["no_cache"] is True
    assert report["phases"]["apply"]["completed"] is False
    assert report["phases_complete"] is False
    assert "libvirt_error" not in report


def test_cacheable_phases_complete_ignores_apply():
    phases = {
        "kvm_host": {"completed": True},
        "vms": {"completed": True},
        "rancher": {"completed": True},
        "apply": {"completed": False, "no_cache": True},
        "finalise": {"completed": True},
    }
    assert cacheable_phases_complete(phases) is True
    phases["rancher"] = {"completed": False}
    assert cacheable_phases_complete(phases) is False
    # Legacy remotes: no no_cache flag, still ignore apply by name
    assert (
        cacheable_phases_complete(
            {
                "kvm_host": {"completed": True},
                "apply": {"completed": False},
            }
        )
        is True
    )


def test_status_report_phases_complete_when_cacheable_done(monkeypatch):
    monkeypatch.setattr(
        "rodeo.service.status.load_state",
        lambda name: {
            "phases": {
                "kvm_host": {"completed": True},
                "vms": {"completed": True},
                "boot": {"completed": True},
                "rancher": {"completed": True},
                "finalise": {"completed": True},
            }
        },
    )
    monkeypatch.setattr("rodeo.service.status.vip_reachable", lambda vip: False)
    monkeypatch.setattr(
        "rodeo.engine.libvirt.LibvirtDriver",
        lambda uri: _FakeLV(),
    )
    cfg = {
        "name": "lab",
        "type": "rancher",
        "network": {"vip": "192.168.122.10"},
        "libvirt": {"uri": "qemu:///system"},
        "vms": {"rancher": {}},
    }
    report = status_report(cfg)
    assert report["phases"]["apply"]["no_cache"] is True
    assert report["phases_complete"] is True


def test_status_report_libvirt_error(monkeypatch):
    monkeypatch.setattr("rodeo.service.status.load_state", lambda name: {})
    monkeypatch.setattr("rodeo.service.status.vip_reachable", lambda vip: False)

    class _Boom:
        def __enter__(self):
            raise RuntimeError("libvirt down")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("rodeo.engine.libvirt.LibvirtDriver", lambda uri: _Boom())

    cfg = {
        "name": "lab",
        "type": "rancher",
        "network": {"vip": "192.168.122.10"},
        "libvirt": {"uri": "qemu:///system"},
        "vms": {"rancher": {}},
    }
    report = status_report(cfg)
    assert report["vms"] == []
    assert report["libvirt_error"] == "libvirt down"
    assert report["vip_reachable"] is False
