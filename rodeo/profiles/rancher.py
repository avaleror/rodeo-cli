"""Rancher-only profile — Rancher Prime on K3s, no Harvester.

The laptop-sized on-ramp: a single VM running K3s + Rancher Prime, for learning
Rancher, Fleet, and cluster management without the cost of a 3-node Harvester HCI
cluster. Reuses the same engine; the pipeline simply omits the Harvester phases
(``pxe_server`` and ``cluster``) and RancherPhase runs in standalone mode.
"""
from __future__ import annotations

from .base import BASE_VERSIONS, RodeoProfile


class RancherProfile(RodeoProfile):
    name = "rancher"
    # 'boot' starts the network + VM (no Harvester cluster, so no pxe_server/cluster).
    phases = ["kvm_host", "vms", "boot", "rancher", "apply", "finalise"]
    vm_names = ["rancher"]
    ansible_phases = frozenset(["kvm_host", "vms"])
    guarded_phases = frozenset(["finalise"])
    no_cache_phases = frozenset(["apply"])

    static_vms = {"rancher": {"ip": "192.168.122.9", "user": "root"}}
    resources = {"rancher": {"memory_mib": 8192, "vcpu": 4, "disk_gb": 60}}
    versions = BASE_VERSIONS
