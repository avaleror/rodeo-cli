"""SUSE Virtualization Rodeo profile — Harvester HCI + Rancher on KVM."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from .base import RodeoProfile

if TYPE_CHECKING:
    from ..engine.runner import DeployEvent, DeployRunner

# Example of loading the Harvester/SUSE Virtualization topology from the declarative definition file
# (rodeo/data/profiles/suse-virt/definition.yaml). The renderer in inventory.py handles generation
# of MACs etc. when not explicit. This is our current focus for the Harvester rodeo.
try:
    from .. import inventory as _inv
except ImportError:
    _inv = None  # type: ignore[assignment]


class SuseVirtProfile(RodeoProfile):
    name = "suse-virt"
    phases = ["kvm_host", "vms", "pxe_server", "cluster", "rancher", "finalise"]
    vm_names = ["harvester1", "harvester2", "harvester3", "rancher"]
    ansible_phases = frozenset(["kvm_host", "vms", "pxe_server"])
    guarded_phases = frozenset(["finalise"])

    def default_cfg(self, config_dir: str | None = None) -> dict:
        # Demonstration of loading from the new topology/inventory definition file
        # (rodeo/data/profiles/suse-virt/definition.yaml).
        # This replaces the previous hardcoded dict.
        # Full version will come from inventory.build_inventory() which will also
        # apply plan overrides and produce the vm_nodes list for Ansible.
        if _inv is not None:
            try:
                inv_cfg = {"type": self.name}
                if config_dir:
                    inv_cfg["config_dir"] = config_dir
                inv = _inv.build_inventory(inv_cfg)
                vms = {}
                for node in inv.get("vm_nodes", []):
                    vms[node["name"]] = {
                        "ip": node["ip"],
                        "user": node.get("ssh_user", "rancher" if node["flavor"] == "harvester" else "root"),
                    }
                # resources and versions stay in the profile for now (they are the other part
                # of the old "hardcoded assumptions"). They will move into the definition too.
                return {
                    "vms": vms,
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
                    # Storage from definition (multi-disk disk selection, etc.)
                    "storage": inv.get("storage", {
                        "device": "",
                        "mount_point": "/var/lib/libvirt/images",
                        "image_dir": "/var/lib/libvirt/images",
                    }),
                }
            except Exception:
                pass  # fall back to static below if definition loading fails

        # Static fallback (identical to what was previously hardcoded directly in this file)
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
            # Storage default (will be overridden by definition when loaded)
            "storage": {
                "device": "",
                "mount_point": "/var/lib/libvirt/images",
                "image_dir": "/var/lib/libvirt/images",
            },
        }

    def run_phase(
        self,
        phase: str,
        runner: "DeployRunner",
        vars_file: Path,
    ) -> Iterator["DeployEvent"]:
        if phase in ("kvm_host", "vms", "pxe_server"):
            yield from runner.stream_ansible(phase, vars_file)
        elif phase == "cluster":
            yield from runner.stream_cluster()
        elif phase == "rancher":
            # Topologies without a Rancher node (e.g. the 2-node 'test' lab) skip
            # the Rancher install/import entirely.
            if "rancher" in runner.cfg.get("vms", {}):
                yield from runner.stream_rancher()
            else:
                from ..engine.runner import LogLine
                yield LogLine("No Rancher node in this topology — skipping rancher phase.")
                runner._last_rc = 0
        elif phase == "finalise":
            yield from runner.stream_finalise()
        else:
            runner._last_rc = 0
