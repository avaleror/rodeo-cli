"""Rancher profile: Rancher Prime on K3s plus downstream clusters, no Harvester.

Rancher on its own VM, plus three clusters Rancher provisions on lab VMs: a
single-node K3s, a single-node RKE2 and a 3-node RKE2 (etcd HA). Sized at the
minimum each distribution needs, for testing. Reuses the same engine; the
pipeline omits the Harvester phases (``pxe_server`` and ``cluster``),
RancherPhase runs in standalone mode, and the ``downstream`` phase creates the
clusters and registers their nodes.
"""
from __future__ import annotations

from .base import BASE_VERSIONS, RodeoProfile, default_success_next_steps

# Latest Rancher Prime and the newest Kubernetes it supports for K3s/RKE2
# (Rancher 2.15.2 release notes and KDM release-v2.15: v1.36.4 is the top).
RANCHER_VERSIONS: dict = {
    **BASE_VERSIONS,
    "rancher":         "2.15.2",
    "k3s":             "v1.36.4+k3s1",
    "cert_manager":    "v1.21.2",
    "downstream_k3s":  "v1.36.4+k3s1",
    "downstream_rke2": "v1.36.4+rke2r1",
}

# Minimum sizing per node: K3s server 2 vCPU / 2 GiB, RKE2 server 2 vCPU / 4 GiB.
DOWNSTREAM_RESOURCES: dict = {
    "k3s-node":  {"memory_mib": 2048, "vcpu": 2, "disk_gb": 20},
    "rke2-node": {"memory_mib": 4096, "vcpu": 2, "disk_gb": 30},
}


class RancherProfile(RodeoProfile):
    name = "rancher"
    # 'boot' starts the network + VMs (no Harvester cluster, so no pxe_server/cluster).
    phases = ["kvm_host", "vms", "boot", "rancher", "downstream", "apply", "finalise", "custom_scripts"]
    vm_names = ["rancher", "k3s", "rke2", "rke2-ha1", "rke2-ha2", "rke2-ha3"]
    ansible_phases = frozenset(["kvm_host", "vms"])
    guarded_phases = frozenset(["finalise", "custom_scripts"])
    no_cache_phases = frozenset(["apply", "custom_scripts"])

    static_vms = {
        "rancher":  {"ip": "192.168.122.9",  "user": "root"},
        "k3s":      {"ip": "192.168.122.41", "user": "root"},
        "rke2":     {"ip": "192.168.122.42", "user": "root"},
        "rke2-ha1": {"ip": "192.168.122.51", "user": "root"},
        "rke2-ha2": {"ip": "192.168.122.52", "user": "root"},
        "rke2-ha3": {"ip": "192.168.122.53", "user": "root"},
    }
    resources = {
        "rancher": {"memory_mib": 8192, "vcpu": 4, "disk_gb": 60},
        **DOWNSTREAM_RESOURCES,
    }
    versions = RANCHER_VERSIONS

    def success_next_steps(self, cfg: dict) -> list[str]:
        lines = default_success_next_steps(cfg)
        clusters = [c.get("name", "") for c in cfg.get("downstream_clusters") or []]
        if clusters:
            lines.append(f"  In Rancher: Cluster Management lists {', '.join(clusters)}")
        return lines
