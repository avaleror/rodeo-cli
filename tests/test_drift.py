"""Drift detection (shared by plan and --reconcile)."""
from __future__ import annotations

from rodeo.drift import DriftReport, VmChange, collect_drift
from rodeo.engine.libvirt import VMInfo


def test_collect_drift_memory_affects_vms_phase():
    cfg = {
        "type": "suse-virt",
        "name": "t",
        "resources": {"harvester": {"memory_mib": 20480, "vcpu": 8}},
        "vms": {"harvester1": {"ip": "192.168.122.11"}},
        "storage": {"image_dir": "/tmp"},
        "versions": {"harvester": "1.8.1"},
        "libvirt": {"uri": "qemu:///system"},
    }
    actual = {
        "vms": {
            "harvester1": VMInfo(
                name="harvester1", state="running", memory_mib=16384, vcpus=8
            ),
        },
        "net_active": True,
    }
    report = collect_drift(cfg, actual=actual)
    assert report.reachable
    assert report.change_count == 1
    assert report.phases_affected == frozenset({"vms"})
    assert report.resource_change_lines() == ["harvester1: memory 16384 → 20480 MiB"]


def test_collect_drift_create_does_not_trigger_reconcile_phases():
    """V1: missing domains show in plan, but --reconcile only fires on memory/vcpu."""
    cfg = {
        "type": "suse-virt",
        "name": "t",
        "resources": {"harvester": {"memory_mib": 16384, "vcpu": 8}},
        "vms": {"harvester1": {"ip": "192.168.122.11"}},
        "storage": {"image_dir": "/tmp"},
        "versions": {"harvester": "1.8.1"},
        "libvirt": {"uri": "qemu:///system"},
    }
    actual = {"vms": {}, "net_active": True}
    report = collect_drift(cfg, actual=actual)
    assert report.create_count >= 1
    assert report.vms_plan_drift
    assert report.phases_affected == frozenset()


def test_collect_drift_unreachable_never_affects_phases():
    report = collect_drift(
        {"type": "suse-virt", "vms": {}, "resources": {}, "storage": {"image_dir": "/tmp"}},
        actual=None,
    )
    assert not report.reachable
    assert report.phases_affected == frozenset()


def test_drift_report_vcpu_line():
    report = DriftReport(
        reachable=True,
        vm_changes=[
            VmChange(
                name="h1", kind="vcpu", desired="x", from_value=6, to_value=10
            )
        ],
    )
    assert report.phases_affected == frozenset({"vms"})
    assert report.resource_change_lines() == ["h1: vcpu 6 → 10"]


def test_collect_drift_dhcp_host_affects_vms_phase(monkeypatch):
    """Missing NAT DHCP reservation must trigger --reconcile like memory drift."""
    from rodeo import drift as drift_mod

    cfg = {
        "type": "suse-virt",
        "name": "t",
        "resources": {"harvester": {"memory_mib": 16384, "vcpu": 8}},
        "vms": {"harvester1": {"ip": "192.168.122.11"}},
        "storage": {"image_dir": "/tmp"},
        "versions": {"harvester": "1.8.1"},
        "libvirt": {"uri": "qemu:///system"},
    }
    monkeypatch.setattr(
        drift_mod,
        "build_inventory",
        lambda _cfg: {
            "vm_nodes": [
                {
                    "name": "harvester1",
                    "mgmt_mac": "02:00:00:0d:62:e1",
                    "ip": "192.168.122.11",
                    "flavor": "harvester",
                }
            ]
        },
    )
    actual = {
        "vms": {
            "harvester1": VMInfo(
                name="harvester1", state="shut off", memory_mib=16384, vcpus=8
            ),
        },
        "net_active": True,
        "net_xml": "<network><ip><dhcp></dhcp></ip></network>",
    }
    report = collect_drift(cfg, actual=actual)
    assert any(c.kind == "dhcp" for c in report.vm_changes)
    assert report.phases_affected == frozenset({"vms"})
    assert any("DHCP reservation" in line for line in report.resource_change_lines())


def test_collect_drift_dhcp_ok_when_host_line_present(monkeypatch):
    from rodeo import drift as drift_mod

    cfg = {
        "type": "suse-virt",
        "name": "t",
        "resources": {"harvester": {"memory_mib": 16384, "vcpu": 8}},
        "vms": {"harvester1": {"ip": "192.168.122.11"}},
        "storage": {"image_dir": "/tmp"},
        "versions": {"harvester": "1.8.1"},
        "libvirt": {"uri": "qemu:///system"},
    }
    monkeypatch.setattr(
        drift_mod,
        "build_inventory",
        lambda _cfg: {
            "vm_nodes": [
                {
                    "name": "harvester1",
                    "mgmt_mac": "02:00:00:0d:62:e1",
                    "ip": "192.168.122.11",
                    "flavor": "harvester",
                }
            ]
        },
    )
    frag = "mac='02:00:00:0d:62:e1' name='harvester1' ip='192.168.122.11'"
    actual = {
        "vms": {
            "harvester1": VMInfo(
                name="harvester1", state="running", memory_mib=16384, vcpus=8
            ),
        },
        "net_active": True,
        "net_xml": f"<network><host {frag}/></network>",
    }
    report = collect_drift(cfg, actual=actual)
    assert not any(c.kind == "dhcp" for c in report.vm_changes)
    assert report.phases_affected == frozenset()
    assert report.ok_count >= 1


def test_collect_drift_missing_vm_is_create_only_not_also_dhcp(monkeypatch):
    """A VM that doesn't exist yet has no DHCP reservation either — 'create'
    already implies that; it must not also show a separate 'dhcp' line."""
    from rodeo import drift as drift_mod

    cfg = {
        "type": "suse-virt",
        "name": "t",
        "resources": {"harvester": {"memory_mib": 16384, "vcpu": 8}},
        "vms": {"harvester1": {"ip": "192.168.122.11"}},
        "storage": {"image_dir": "/tmp"},
        "versions": {"harvester": "1.8.1"},
        "libvirt": {"uri": "qemu:///system"},
    }
    monkeypatch.setattr(
        drift_mod,
        "build_inventory",
        lambda _cfg: {
            "vm_nodes": [
                {
                    "name": "harvester1",
                    "mgmt_mac": "02:00:00:0d:62:e1",
                    "ip": "192.168.122.11",
                    "flavor": "harvester",
                }
            ]
        },
    )
    actual = {
        "vms": {},
        "net_active": True,
        "net_xml": "<network><ip><dhcp></dhcp></ip></network>",
    }
    report = collect_drift(cfg, actual=actual)
    kinds = [c.kind for c in report.vm_changes if c.name == "harvester1"]
    assert kinds == ["create"]
