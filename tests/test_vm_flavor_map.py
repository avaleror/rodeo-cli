"""vm_flavor_map — definition-driven VM sizing keys for plan/status."""
from __future__ import annotations

from rodeo.config import load_config
from rodeo.inventory import plan_vm_rows, vm_flavor_map


def test_vm_flavor_map_suse_edge():
    cfg = load_config("rodeo-plan.yaml", params=("type=suse-edge",))
    flavors = vm_flavor_map(cfg)
    assert flavors["rancher"] == "rancher"
    assert flavors["eib"] == "eib"
    assert flavors["edge1"] == "edge-node"
    assert flavors["edge4"] == "edge-node"


def test_vm_flavor_map_suse_virt():
    cfg = load_config("rodeo-plan.yaml")
    flavors = vm_flavor_map(cfg)
    assert flavors["harvester1"] == "harvester"
    assert flavors["rancher"] == "rancher"


def test_plan_vm_rows_follows_definition_not_stale_cfg_vms():
    """-P type= must not leave plan iterating the wrong profile's vms dict."""
    cfg = load_config("rodeo-plan.yaml", params=("type=suse-edge",))
    names = [name for name, _ in plan_vm_rows(cfg)]
    assert "edge1" in names
    assert "harvester1" not in names