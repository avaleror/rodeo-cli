"""Host-context adaptation overlays."""
from __future__ import annotations

from rodeo import host_context as host_context_mod
from rodeo.host_context import AWS_HARVESTER_DISK_GB_TOTAL, apply_host_context


def _base_aws_cfg(disk_gb: int = 320) -> dict:
    return {
        "deployment_target": "aws",
        "type": "suse-virt",
        "name": "t",
        "resources": {"harvester": {"memory_mib": 16384, "vcpu": 8, "disk_gb": disk_gb}},
        "storage": {"image_dir": "/var/lib/libvirt/images"},
        "libvirt": {"uri": "qemu:///system"},
    }


def test_aws_raises_harvester_disk_and_sets_nvme_backend(monkeypatch):
    """Single-node pool: the whole 1200 GB budget lands on it, same as the
    historical per-node floor for the 1-node case."""
    monkeypatch.setattr(host_context_mod, "_count_flavor_nodes", lambda cfg, flavor: 1)
    cfg = _base_aws_cfg(320)
    out, notes = apply_host_context(cfg)
    assert out["resources"]["harvester"]["disk_gb"] == AWS_HARVESTER_DISK_GB_TOTAL
    assert out["storage"]["backend"] == "nvme"
    assert out["libvirt"]["disk_cache"] == "none"
    assert out["libvirt"]["disk_io"] == "native"
    assert any("disk_gb" in n for n in notes)
    # Original unchanged
    assert cfg["resources"]["harvester"]["disk_gb"] == 320


def test_aws_splits_disk_floor_across_harvester_node_count(monkeypatch):
    """The 1200 GB budget is for the whole pool, not per node: a 3-node
    profile must not demand ~3.6 TiB (the 2026-07-30 design bug, caught live
    2026-09-11 — i7i.8xlarge's single NVMe device is ~3.4 TiB)."""
    monkeypatch.setattr(host_context_mod, "_count_flavor_nodes", lambda cfg, flavor: 3)
    cfg = _base_aws_cfg(320)
    cfg["resources"]["rancher"] = {"disk_gb": 60}
    out, notes = apply_host_context(cfg)
    per_node = out["resources"]["harvester"]["disk_gb"]
    assert per_node * 3 <= AWS_HARVESTER_DISK_GB_TOTAL
    assert per_node == AWS_HARVESTER_DISK_GB_TOTAL // 3
    assert any("3 nodes" in n for n in notes)
    # Rancher isn't in the floor table — untouched.
    assert out["resources"]["rancher"]["disk_gb"] == 60


def test_aws_two_node_harvester_gets_more_per_node_than_three(monkeypatch):
    cfg_2 = _base_aws_cfg(320)
    cfg_3 = _base_aws_cfg(320)
    monkeypatch.setattr(host_context_mod, "_count_flavor_nodes", lambda cfg, flavor: 2)
    two_out, _ = apply_host_context(cfg_2)
    monkeypatch.setattr(host_context_mod, "_count_flavor_nodes", lambda cfg, flavor: 3)
    three_out, _ = apply_host_context(cfg_3)
    assert (
        two_out["resources"]["harvester"]["disk_gb"]
        > three_out["resources"]["harvester"]["disk_gb"]
    )


def test_count_flavor_nodes_matches_bundled_default_topology():
    """Unmocked integration check, no definition.yaml override: locks in the
    bundled default suse-virt topology's actual harvester node count, so a
    future change to that default can't silently change the AWS disk-floor
    math without a test noticing."""
    cfg = {"type": "suse-virt", "name": "t"}
    assert host_context_mod._count_flavor_nodes(cfg, "harvester") == 3
    assert host_context_mod._count_flavor_nodes(cfg, "rancher") == 1


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
