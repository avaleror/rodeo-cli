"""SUSE Virtualization Rodeo profile — Harvester HCI + Rancher on KVM.

Topology and versions come from the declarative definition file
(rodeo/data/platforms/suse-virt/definition.yaml); inventory.py renders it,
generating MACs etc. when not explicit. The class attributes below are only a
fallback for dev/test runs without the packaged definition.
"""
from __future__ import annotations

from .base import BASE_VERSIONS, RodeoProfile


class SuseVirtProfile(RodeoProfile):
    name = "suse-virt"
    phases = ["kvm_host", "vms", "pxe_server", "cluster", "rancher", "apply", "finalise"]
    vm_names = ["harvester1", "harvester2", "harvester3", "rancher"]
    ansible_phases = frozenset(["kvm_host", "vms", "pxe_server"])
    guarded_phases = frozenset(["finalise"])
    no_cache_phases = frozenset(["apply"])

    # Versions are authoritative in definition.yaml; these are only hit without
    # the packaged data. Changing the definition drives idempotent upgrades on
    # re-run (helm upgrade --install for Rancher/cert-manager; K3s installer).
    versions_from_definition = True
    versions = {**BASE_VERSIONS, "harvester": "1.8.0"}

    resources = {
        "harvester": {"memory_mib": 16384, "vcpu": 8, "disk_gb": 270},
        "rancher":   {"memory_mib": 8192,  "vcpu": 4, "disk_gb": 60},
    }

    static_vms = {
        "harvester1": {"ip": "192.168.122.11", "user": "rancher"},
        "harvester2": {"ip": "192.168.122.12", "user": "rancher"},
        "harvester3": {"ip": "192.168.122.13", "user": "rancher"},
        "rancher":    {"ip": "192.168.122.9",  "user": "root"},
    }

    def _default_user(self, node: dict) -> str:
        return "rancher" if node.get("flavor") == "harvester" else "root"
