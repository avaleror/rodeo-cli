"""Rancher-only profile and RancherPhase standalone mode."""
from __future__ import annotations

import yaml

from rodeo import config, inventory, secretgen
from rodeo.engine.rancher import RancherPhase
from rodeo.labseed import seed_lab
from rodeo.profiles import get_profile


def test_profile_phases_skip_harvester():
    p = get_profile("rancher")
    assert p.phases == ["kvm_host", "vms", "rancher", "finalise"]
    assert "pxe_server" not in p.phases
    assert "cluster" not in p.phases
    assert p.vm_names == ["rancher"]


def test_inventory_has_single_rancher_node():
    inv = inventory.build_inventory({"type": "rancher", "name": "r"})
    nodes = inv["vm_nodes"]
    assert len(nodes) == 1
    assert nodes[0]["name"] == "rancher"
    assert nodes[0]["flavor"] == "rancher"
    assert inv["harvester_node_names"] == []


def test_standalone_detected_for_rancher_only():
    cfg = {"network": {"vip": "192.168.122.10"}, "vms": {"rancher": {"ip": "192.168.122.9"}}}
    assert RancherPhase(cfg).standalone is True


def test_not_standalone_with_harvester_nodes():
    cfg = {
        "network": {"vip": "192.168.122.10"},
        "vms": {"harvester1": {"ip": "192.168.122.11"}, "rancher": {"ip": "192.168.122.9"}},
    }
    assert RancherPhase(cfg).standalone is False


def test_seed_and_load_rancher_lab(tmp_path):
    lab = seed_lab("rancher", tmp_path / "r1")
    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    assert plan["type"] == "rancher"
    assert "harvester" not in plan.get("resources", {})

    secretgen.ensure_secrets_file(tmp_path / ".rodeo" / "secrets.yaml")
    cfg = config.load_config("rodeo-plan.yaml", config_dir=str(lab))
    config.validate_config(cfg)
    assert list(cfg["vms"]) == ["rancher"]
