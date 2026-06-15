"""Rancher-only profile — Rancher Prime on K3s, no Harvester.

The laptop-sized on-ramp: a single VM running K3s + Rancher Prime, for learning
Rancher, Fleet, and cluster management without the cost of a 3-node Harvester HCI
cluster. Reuses the same engine; the pipeline simply omits the Harvester phases
(``pxe_server`` and ``cluster``) and RancherPhase runs in standalone mode.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from .base import RodeoProfile

if TYPE_CHECKING:
    from ..engine.runner import DeployEvent, DeployRunner

try:
    from .. import inventory as _inv
except ImportError:
    _inv = None  # type: ignore[assignment]


class RancherProfile(RodeoProfile):
    name = "rancher"
    # 'boot' starts the network + VM (no Harvester cluster, so no pxe_server/cluster).
    phases = ["kvm_host", "vms", "boot", "rancher", "finalise"]
    vm_names = ["rancher"]
    ansible_phases = frozenset(["kvm_host", "vms"])
    guarded_phases = frozenset(["finalise"])

    def default_cfg(self, config_dir: str | None = None) -> dict:
        vms = {"rancher": {"ip": "192.168.122.9", "user": "root"}}
        storage = {
            "device": "",
            "mount_point": "/var/lib/libvirt/images",
            "image_dir": "/var/lib/libvirt/images",
        }
        if _inv is not None:
            try:
                inv_cfg: dict = {"type": self.name}
                if config_dir:
                    inv_cfg["config_dir"] = config_dir
                inv = _inv.build_inventory(inv_cfg)
                rendered = {
                    node["name"]: {
                        "ip": node["ip"],
                        "user": node.get("ssh_user", "root"),
                    }
                    for node in inv.get("vm_nodes", [])
                }
                if rendered:
                    vms = rendered
                storage = inv.get("storage", storage)
            except Exception:
                pass  # fall back to the static defaults above

        return {
            "vms": vms,
            "resources": {
                "rancher": {"memory_mib": 8192, "vcpu": 4, "disk_gb": 60},
            },
            "versions": {
                "rancher":      "2.13.1",
                "k3s":          "v1.31.4+k3s1",
                "cert_manager": "v1.16.2",
            },
            "storage": storage,
        }

    def run_phase(
        self,
        phase: str,
        runner: "DeployRunner",
        vars_file: Path,
    ) -> Iterator["DeployEvent"]:
        if phase in ("kvm_host", "vms"):
            yield from runner.stream_ansible(phase, vars_file)
        elif phase == "boot":
            yield from runner.stream_boot()
        elif phase == "rancher":
            yield from runner.stream_rancher()
        elif phase == "finalise":
            yield from runner.stream_finalise()
        else:
            runner._last_rc = 0
