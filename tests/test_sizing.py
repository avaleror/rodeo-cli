"""Instruqt resource presets and guest vCPU budget."""
from __future__ import annotations

import yaml

from rodeo import sizing
from rodeo.labseed import seed_lab
from rodeo import preflight


def test_budget_ratio_on_32_and_40_cpu_hosts():
    assert sizing.instruqt_vcpu_budget(32) == 22
    assert sizing.instruqt_vcpu_budget(40) == 28


def test_presets_size_harvester_for_32_vcpu_builder():
    presets = sizing.compute_instruqt_presets(32, {"harvester": 3, "rancher": 1})
    assert presets["harvester"]["vcpu"] == 6
    assert presets["harvester"]["memory_mib"] == 20480
    assert presets["rancher"]["vcpu"] == 4
    # 3*6 + 4 = 22 → exactly the soft budget
    assert 3 * presets["harvester"]["vcpu"] + presets["rancher"]["vcpu"] == 22


def test_presets_cap_harvester_at_8_on_large_builder():
    presets = sizing.compute_instruqt_presets(40, {"harvester": 3, "rancher": 1})
    assert presets["harvester"]["vcpu"] == 8
    assert 3 * 8 + 4 == 28  # within 70% of 40


def test_apply_presets_only_touches_declared_flavors_preserves_disk():
    plan = {
        "resources": {
            "harvester": {"memory_mib": 16384, "vcpu": 10, "disk_gb": 270},
        }
    }
    notes = sizing.apply_instruqt_resource_presets(
        plan, host_cpus=32, flavor_counts={"harvester": 2, "rancher": 1}
    )
    # budget 22 − rancher 4 = 18 → 9/node → capped at 8
    assert plan["resources"]["harvester"]["vcpu"] == 8
    assert plan["resources"]["harvester"]["disk_gb"] == 270
    assert "rancher" not in plan["resources"]
    assert any("vcpu" in n for n in notes)


def test_flavor_counts_from_harvester_definition(tmp_path):
    from rodeo.labseed import example_dir

    counts = sizing.flavor_counts_from_definition(example_dir("harvester") / "definition.yaml")
    assert counts == {"harvester": 3, "rancher": 1}


def test_seed_lab_instruqt_applies_presets(tmp_path, monkeypatch):
    import os

    monkeypatch.setattr(os, "cpu_count", lambda: 32)
    lab = seed_lab("harvester", tmp_path / "iq", deployment_target="instruqt")
    data = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    assert data["deployment_target"] == "instruqt"
    assert data["resources"]["harvester"]["vcpu"] == 6
    assert data["resources"]["harvester"]["memory_mib"] == 20480
    assert data["resources"]["rancher"]["vcpu"] == 4
    # disk left alone from the bundled example
    assert data["resources"]["harvester"]["disk_gb"] == 270


def test_seed_lab_baremetal_keeps_example_vcpu(tmp_path):
    lab = seed_lab("harvester", tmp_path / "bm", deployment_target="baremetal")
    data = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    # Bundled harvester example is already at 10 / 20 GiB for larger hosts.
    assert data["resources"]["harvester"]["vcpu"] == 10
    assert data["resources"]["harvester"]["memory_mib"] == 20480


def test_overcommit_detail_warns_when_guest_exceeds_budget():
    cfg = {
        "deployment_target": "instruqt",
        "type": "suse-virt",
        "name": "t",
        "libvirt": {"uri": "qemu:///system"},
        "storage": {"image_dir": "/tmp"},
        "versions": {"harvester": "1.8.1"},
        "resources": {
            "harvester": {"memory_mib": 20480, "vcpu": 10},
            "rancher": {"memory_mib": 8192, "vcpu": 4},
        },
        "vms": {
            "harvester1": {"ip": "192.168.122.11"},
            "harvester2": {"ip": "192.168.122.12"},
            "harvester3": {"ip": "192.168.122.13"},
            "rancher": {"ip": "192.168.122.9"},
        },
    }
    # 3*10+4 = 34 > 22 on a 32-vCPU host
    detail = sizing.vcpu_overcommit_detail(cfg, host_cpus=32)
    assert detail is not None
    assert "34" in detail

    assert sizing.vcpu_overcommit_detail(cfg, host_cpus=64) is None
    cfg["deployment_target"] = "baremetal"
    assert sizing.vcpu_overcommit_detail(cfg, host_cpus=32) is None


def test_run_preflight_warns_on_instruqt_vcpu_overcommit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(preflight.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0)
    cfg = {
        "name": "over",
        "deployment_target": "instruqt",
        "type": "suse-virt",
        "libvirt": {"uri": "qemu:///system"},
        "versions": {"harvester": "1.8.1"},
        "resources": {
            "harvester": {"memory_mib": 20480, "vcpu": 10, "disk_gb": 50},
            "rancher": {"memory_mib": 8192, "vcpu": 4, "disk_gb": 30},
        },
        "vms": {
            "harvester1": {},
            "harvester2": {},
            "harvester3": {},
            "rancher": {},
        },
        "storage": {"image_dir": str(tmp_path)},
    }
    preflight.run_preflight(cfg, tmp_path, phases_to_run=["vms"])
    out = capsys.readouterr().out
    assert "guest vCPU budget" in out
    assert "⚠" in out
