"""rancher-test profile: Rancher Prime on K3s plus two single-node clusters.

The smaller sibling of ``rancher``: Rancher provisions a single-node K3s and a
single-node RKE2 cluster on lab VMs. Same engine, phases and sizing per node.
"""
from __future__ import annotations

from .rancher import RancherProfile

_VM_NAMES = ["rancher", "k3s", "rke2"]


class RancherTestProfile(RancherProfile):
    name = "rancher-test"
    vm_names = _VM_NAMES
    static_vms = {name: RancherProfile.static_vms[name] for name in _VM_NAMES}
