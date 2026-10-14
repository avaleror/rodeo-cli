"""Rancher profile (no Harvester) and RancherPhase standalone mode."""
from __future__ import annotations

import yaml

from rodeo import config, inventory, secretgen
from rodeo.engine.rancher import RancherPhase
from rodeo.labseed import seed_lab
from rodeo.profiles import get_profile


def test_profile_phases_skip_harvester():
    p = get_profile("rancher")
    assert p.phases == [
        "kvm_host", "vms", "boot", "rancher", "downstream", "apply", "finalise", "custom_scripts",
    ]
    assert "pxe_server" not in p.phases
    assert "cluster" not in p.phases
    # 'boot' starts the network + VMs in place of the Harvester ClusterPhase.
    assert "boot" in p.phases
    assert p.vm_names == ["rancher", "k3s", "rke2", "rke2-ha1", "rke2-ha2", "rke2-ha3"]


def test_inventory_has_rancher_and_downstream_nodes():
    inv = inventory.build_inventory({"type": "rancher", "name": "r"})
    nodes = {n["name"]: n["flavor"] for n in inv["vm_nodes"]}
    assert nodes == {
        "rancher": "rancher", "k3s": "k3s-node", "rke2": "rke2-node",
        "rke2-ha1": "rke2-node", "rke2-ha2": "rke2-node", "rke2-ha3": "rke2-node",
    }
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
    assert list(cfg["vms"]) == ["rancher", "k3s", "rke2", "rke2-ha1", "rke2-ha2", "rke2-ha3"]
