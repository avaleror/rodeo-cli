"""Host-context adaptation overlays."""
from __future__ import annotations

from rodeo.host_context import (
    AWS_HARVESTER_DISK_GB,
    AWS_RANCHER_DISK_GB,
    apply_host_context,
)


def test_aws_raises_harvester_disk_and_sets_nvme_backend():
    cfg = {
        "deployment_target": "aws",
        "resources": {"harvester": {"memory_mib": 16384, "vcpu": 8, "disk_gb": 250}},
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
    assert cfg["resources"]["harvester"]["disk_gb"] == 250


def test_aws_floor_is_flat_per_node_not_scaled_by_node_count():
    """A flat per-node floor regardless of how many Harvester nodes the
    profile has — deliberately NOT a shared pool budget. The earlier
    per-node-1200 design (2026-07-30) and its same-day total-pool-budget
    fix both got corrected 2026-09-11: real target is a flat
    AWS_HARVESTER_DISK_GB per Harvester node / AWS_RANCHER_DISK_GB for
    Rancher, leaving the rest of the NVMe device free."""
    cfg = {
        "deployment_target": "aws",
        "resources": {
            "harvester": {"disk_gb": 250},
            "rancher": {"disk_gb": 40},
        },
        "storage": {"image_dir": "/var/lib/libvirt/images"},
        "libvirt": {"uri": "qemu:///system"},
    }
    out, notes = apply_host_context(cfg)
    assert out["resources"]["harvester"]["disk_gb"] == AWS_HARVESTER_DISK_GB
    assert out["resources"]["rancher"]["disk_gb"] == AWS_RANCHER_DISK_GB
    assert any("resources.harvester.disk_gb" in n for n in notes)
    assert any("resources.rancher.disk_gb" in n for n in notes)


def test_aws_does_not_raise_disk_already_above_floor():
    """An explicit disk_gb already at or above the AWS floor must not change —
    only raise when below."""
    cfg = {
        "deployment_target": "aws",
        "resources": {
            "harvester": {"disk_gb": AWS_HARVESTER_DISK_GB + 100},
            "rancher": {"disk_gb": AWS_RANCHER_DISK_GB},
        },
        "storage": {},
        "libvirt": {},
    }
    out, notes = apply_host_context(cfg)
    assert out["resources"]["harvester"]["disk_gb"] == AWS_HARVESTER_DISK_GB + 100
    assert out["resources"]["rancher"]["disk_gb"] == AWS_RANCHER_DISK_GB
    assert not any("disk_gb" in n for n in notes)


def test_aws_disk_floor_override_lowers_floor_for_that_flavor():
    """resources.<flavor>.disk_floor_override_gb opts a plan out of the flat
    500 GB floor — needed for a 2-node profile on a smaller instance than the
    3-node profiles were floored for (2 x 500 + 60 = 1060 GB doesn't fit
    m8id.4xlarge's 950 GB NVMe device; 2 x 400 + 60 = 860 GB does)."""
    cfg = {
        "deployment_target": "aws",
        "resources": {
            "harvester": {"disk_gb": 250, "disk_floor_override_gb": 400},
            "rancher": {"disk_gb": 40},
        },
        "storage": {},
        "libvirt": {},
    }
    out, notes = apply_host_context(cfg)
    assert out["resources"]["harvester"]["disk_gb"] == 400
    # Rancher has no override — still gets the normal flat floor.
    assert out["resources"]["rancher"]["disk_gb"] == AWS_RANCHER_DISK_GB
    assert any("resources.harvester.disk_gb: 250 → 400" in n for n in notes)


def test_aws_disk_floor_override_still_never_shrinks_explicit_larger():
    """The override changes the floor, not the "never shrink" rule — an
    explicit disk_gb already above the override must survive unchanged."""
    cfg = {
        "deployment_target": "aws",
        "resources": {"harvester": {"disk_gb": 900, "disk_floor_override_gb": 400}},
        "storage": {},
        "libvirt": {},
    }
    out, notes = apply_host_context(cfg)
    assert out["resources"]["harvester"]["disk_gb"] == 900
    assert not any("disk_gb" in n for n in notes)


def test_aws_existing_profiles_unaffected_by_override_mechanism():
    """No disk_floor_override_gb set (every profile shipped before this
    feature) — behavior is byte-for-byte the same flat 500 GB floor."""
    cfg = {
        "deployment_target": "aws",
        "resources": {"harvester": {"disk_gb": 250}},
        "storage": {},
        "libvirt": {},
    }
    out, _ = apply_host_context(cfg)
    assert out["resources"]["harvester"]["disk_gb"] == AWS_HARVESTER_DISK_GB


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


def test_aws_suse_edge_forces_self_signed_tls_and_raises_headroom():
    """suse-edge on aws must not default to letsEncrypt: rodeo's managed SG
    (rodeo/providers/aws.py MANAGED_SG_PORTS) only opens 22/8443/30002 to the
    operator's own IP — port 80 is never reachable, so ACME can never
    complete, and 443 (where letsEncrypt/Traefik would serve) isn't open
    either. Values below match the deleted suse-edge-aws example profile."""
    cfg = {
        "type": "suse-edge",
        "deployment_target": "aws",
        "rancher_tls": {"source": "letsEncrypt", "email": "admin@example.com"},
        "resources": {
            "rancher": {"memory_mib": 8192, "vcpu": 4, "disk_gb": 60},
            "eib": {"memory_mib": 12288, "vcpu": 4, "disk_gb": 100},
            "edge-node": {"memory_mib": 4096, "vcpu": 2, "disk_gb": 20},
        },
    }
    out, notes = apply_host_context(cfg)
    assert out["rancher_tls"]["source"] == "secret"
    assert out["rancher_tls"]["email"] == "admin@example.com"
    assert out["resources"]["rancher"]["memory_mib"] == 12288
    assert out["resources"]["rancher"]["disk_gb"] == 60
    assert out["resources"]["eib"]["memory_mib"] == 16384
    assert out["resources"]["eib"]["disk_gb"] == 150
    assert out["resources"]["edge-node"]["memory_mib"] == 4096
    assert out["resources"]["edge-node"]["disk_gb"] == 25
    assert any("rancher_tls.source" in n for n in notes)


def test_aws_suse_edge_leaves_explicit_non_default_tls_alone():
    """Only the still-default 'letsEncrypt' is replaced — a plan that already
    chose 'secret' (or anything else) is left untouched."""
    cfg = {
        "type": "suse-edge",
        "deployment_target": "aws",
        "rancher_tls": {"source": "secret"},
        "resources": {"rancher": {"memory_mib": 20000, "disk_gb": 60}},
    }
    out, notes = apply_host_context(cfg)
    assert out["rancher_tls"]["source"] == "secret"
    assert not any("rancher_tls.source" in n for n in notes)
    # Never shrinks an explicit larger plan.
    assert out["resources"]["rancher"]["memory_mib"] == 20000


def test_aws_suse_edge_overlay_does_not_run_on_baremetal_or_other_types():
    baremetal = {
        "type": "suse-edge",
        "deployment_target": "baremetal",
        "rancher_tls": {"source": "letsEncrypt"},
        "resources": {"rancher": {"memory_mib": 8192, "disk_gb": 60}},
    }
    out, _ = apply_host_context(baremetal)
    assert out["rancher_tls"]["source"] == "letsEncrypt"
    assert out["resources"]["rancher"]["memory_mib"] == 8192

    harvester_on_aws = {
        "type": "suse-virt",
        "deployment_target": "aws",
        "resources": {"harvester": {"disk_gb": 0}},
    }
    out2, _ = apply_host_context(harvester_on_aws)
    assert "rancher_tls" not in out2
