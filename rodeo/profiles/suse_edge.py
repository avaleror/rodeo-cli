"""SUSE Edge 3.6 rodeo profile — Rancher Prime + Elemental + EIB + edge nodes on KVM."""
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


class SuseEdgeProfile(RodeoProfile):
    name = "suse-edge"
    # pxe_server and cluster phases are suse-virt specific — Edge uses cloud-init VMs.
    # elemental installs Elemental Operator (CRDs + Operator) on the management cluster
    # after Rancher Prime is up. Edge nodes then register via TPM + Elemental.
    phases = ["kvm_host", "vms", "rancher", "elemental", "finalise"]
    vm_names = ["rancher", "eib", "edge1", "edge2", "edge3"]
    ansible_phases = frozenset(["kvm_host", "vms"])
    guarded_phases = frozenset(["finalise"])

    def default_cfg(self, config_dir: str | None = None) -> dict:
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
                        "user": node.get("ssh_user", "root"),
                    }
                return {
                    "vms": vms,
                    "resources": _RESOURCES,
                    "versions": _VERSIONS,
                    "storage": inv.get("storage", _STORAGE),
                    "harvester_node_names": [],
                    "rancher_tls": _RANCHER_TLS,
                }
            except Exception:
                pass

        return {
            "vms": {
                "rancher": {"ip": "192.168.122.9",  "user": "root"},
                "eib":     {"ip": "192.168.122.20", "user": "root"},
                "edge1":   {"ip": "192.168.122.31", "user": "root"},
                "edge2":   {"ip": "192.168.122.32", "user": "root"},
                "edge3":   {"ip": "192.168.122.33", "user": "root"},
            },
            "resources": _RESOURCES,
            "versions": _VERSIONS,
            "storage": _STORAGE,
            "harvester_node_names": [],
            "rancher_tls": _RANCHER_TLS,
        }

    def run_phase(
        self,
        phase: str,
        runner: "DeployRunner",
        vars_file: Path,
    ) -> Iterator["DeployEvent"]:
        if phase in ("kvm_host", "vms"):
            yield from runner.stream_ansible(phase, vars_file)
        elif phase == "rancher":
            if "rancher" in runner.cfg.get("vms", {}):
                yield from runner.stream_rancher()
            else:
                from ..engine.runner import LogLine
                yield LogLine("No Rancher node in topology — skipping rancher phase.")
                runner._last_rc = 0
        elif phase == "elemental":
            if "rancher" in runner.cfg.get("vms", {}):
                yield from runner.stream_elemental()
            else:
                from ..engine.runner import LogLine
                yield LogLine("No Rancher node in topology — skipping elemental phase.")
                runner._last_rc = 0
        elif phase == "finalise":
            yield from runner.stream_finalise()
        else:
            runner._last_rc = 0


# SUSE Edge 3.6 component versions.
# Management cluster uses K3s (lab) — production SUSE Edge uses RKE2 v1.35.3+rke2r3.
_VERSIONS = {
    "rancher":                "2.14.1",
    "k3s":                    "v1.35.3+k3s1",
    "cert_manager":           "v1.16.3",
    "elemental_operator_crds": "1.9.0",
    "elemental_operator":     "1.9.0",
    "eib":                    "1.3.3.1",
}

_RESOURCES = {
    # Same sizing as the rancher VM in the suse-virt profile.
    "rancher":    {"memory_mib": 8192,  "vcpu": 4, "disk_gb": 60},
    # EIB VM: needs room for the EIB container + base OS images + built artifacts.
    "eib":        {"memory_mib": 12288, "vcpu": 4, "disk_gb": 100},
    # Edge nodes: minimum for SL Micro 6.2 + Elemental agent.
    "edge-node":  {"memory_mib": 4096,  "vcpu": 2, "disk_gb": 20},
}

_STORAGE = {
    "device": "",
    "mount_point": "/var/lib/libvirt/images",
    "image_dir": "/var/lib/libvirt/images",
}

# Let's Encrypt via sslip.io — Rancher is accessed at rancher.<ext-ip>.sslip.io:443
# via Traefik ingress (no NodePort). Email is used for ACME registration.
# Override email in rodeo-plan.yaml under rancher_tls.email.
_RANCHER_TLS = {
    "source": "letsEncrypt",
    "email": "admin@example.com",
}
