"""Host-context adaptation overlays."""
from __future__ import annotations

from rodeo.host_context import AWS_HARVESTER_DISK_GB, apply_host_context


def test_aws_raises_harvester_disk_and_sets_nvme_backend():
    cfg = {
        "deployment_target": "aws",
        "type": "suse-virt",
        "name": "t",
        "resources": {"harvester": {"memory_mib": 16384, "vcpu": 8, "disk_gb": 320}},
        "storage": {"image_dir": "/var/lib/libvirt/images"},
        "libvirt": {"uri": "qemu:///system"},
    }
    out, notes = apply_host_context(cfg)
    assert out["resources"]["harvester"]["disk_gb"] == AWS_HARVESTER_DISK_GB
    assert out["storage"]["backend"] == "nvme"
    assert out["libvirt"]["disk_cache"] == "none"
    assert out["libvirt"]["disk_io"] == "native"
    assert any("disk_gb" in n for n in notes)
    # Original unchanged
    assert cfg["resources"]["harvester"]["disk_gb"] == 320


def test_aws_does_not_shrink_larger_disk():
    cfg = {
        "deployment_target": "aws",
        "resources": {"harvester": {"disk_gb": 2000}},
        "storage": {},
        "libvirt": {},
    }
    out, _ = apply_host_context(cfg)
    assert out["resources"]["harvester"]["disk_gb"] == 2000


def test_instruqt_does_not_force_aws_disk():
    cfg = {
        "deployment_target": "instruqt",
        "resources": {"harvester": {"memory_mib": 16384, "vcpu": 8, "disk_gb": 320}},
        "storage": {},
    }
    out, _ = apply_host_context(cfg, host_facts={"cpus": 32, "flavor_counts": {}})
    assert out["resources"]["harvester"]["disk_gb"] == 320
    assert out["storage"].get("backend") != "nvme"


def test_baremetal_warns_when_disk_short():
    cfg = {
        "deployment_target": "baremetal",
        "type": "suse-virt",
        "name": "t",
        "resources": {"harvester": {"disk_gb": 1200}},
        "vms": {"harvester1": {"ip": "192.168.122.11"}},
        "storage": {"image_dir": "/tmp"},
        "versions": {"harvester": "1.8.1"},
        "libvirt": {"uri": "qemu:///system"},
    }
    _, notes = apply_host_context(cfg, host_facts={"disk_free_gib": 100})
    assert any(n.startswith("warn:") for n in notes)


def test_baremetal_ec2_nvme_fact_enables_backend():
    cfg = {
        "deployment_target": "baremetal",
        "resources": {},
        "storage": {},
    }
    out, notes = apply_host_context(cfg, host_facts={"has_nvme": True})
    assert out["storage"]["backend"] == "nvme"
    assert any("nvme" in n for n in notes)
