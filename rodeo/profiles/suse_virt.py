"""SUSE Virtualization Rodeo profile — Harvester HCI + Rancher on KVM."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from .base import RodeoProfile

if TYPE_CHECKING:
    from ..engine.runner import DeployEvent, DeployRunner


class SuseVirtProfile(RodeoProfile):
    name = "suse-virt"
    phases = ["kvm_host", "vms", "cluster", "rancher", "finalise"]
    vm_names = ["harvester1", "harvester2", "harvester3", "rancher"]
    ansible_phases = frozenset(["kvm_host", "vms"])
    guarded_phases = frozenset(["finalise"])

    def default_cfg(self) -> dict:
        return {
            "vms": {
                "harvester1": {"ip": "192.168.122.11", "user": "rancher"},
                "harvester2": {"ip": "192.168.122.12", "user": "rancher"},
                "harvester3": {"ip": "192.168.122.13", "user": "rancher"},
                "rancher":    {"ip": "192.168.122.9",  "user": "root"},
            },
            "resources": {
                "harvester": {"memory_mib": 16384, "vcpu": 8, "disk_gb": 270},
                "rancher":   {"memory_mib": 8192,  "vcpu": 4, "disk_gb": 60},
            },
            "versions": {
                "harvester":    "1.8.0",
                "rancher":      "2.13.1",
                "k3s":          "v1.31.4+k3s1",
                "cert_manager": "v1.16.2",
            },
        }

    def run_phase(
        self,
        phase: str,
        runner: "DeployRunner",
        vars_file: Path,
    ) -> Iterator["DeployEvent"]:
        if phase in ("kvm_host", "vms"):
            yield from runner.stream_ansible(phase, vars_file)
        elif phase == "cluster":
            yield from runner.stream_cluster()
        elif phase == "rancher":
            yield from runner.stream_rancher()
        elif phase == "finalise":
            yield from runner.stream_finalise()
        else:
            runner._last_rc = 0
