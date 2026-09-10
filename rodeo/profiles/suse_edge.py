"""SUSE Edge 3.6 rodeo profile — Rancher Prime + Elemental + EIB + edge nodes on KVM."""
from __future__ import annotations

from .base import BASE_VERSIONS, RodeoProfile


class SuseEdgeProfile(RodeoProfile):
    name = "suse-edge"
    # pxe_server and cluster phases are suse-virt specific — Edge uses cloud-init VMs.
    # elemental installs Elemental Operator (CRDs + Operator) on the management cluster
    # after Rancher Prime is up. Edge nodes then register via TPM + Elemental.
    phases = ["kvm_host", "vms", "boot", "rancher", "elemental", "apply", "finalise"]
    vm_names = ["rancher", "eib", "edge1", "edge2", "edge3", "edge4"]
    ansible_phases = frozenset(["kvm_host", "vms"])
    guarded_phases = frozenset(["finalise"])
    no_cache_phases = frozenset(["apply"])

    static_vms = {
        "rancher": {"ip": "192.168.122.9",  "user": "root"},
        "eib":     {"ip": "192.168.122.20", "user": "root"},
        "edge1":   {"ip": "192.168.122.31", "user": "root"},
        "edge2":   {"ip": "192.168.122.32", "user": "root"},
        "edge3":   {"ip": "192.168.122.33", "user": "root"},
        "edge4":   {"ip": "192.168.122.34", "user": "root"},
    }

    # SUSE Edge 3.6 component versions.
    # Management cluster uses K3s (lab) — production SUSE Edge uses RKE2 v1.35.3+rke2r3.
    versions = {
        **BASE_VERSIONS,
        "elemental_operator_crds": "1.9.0",
        "elemental_operator":      "1.9.0",
        "elemental_ui_extension":  "3.0.1",
        "eib":                     "1.3.3.1",
    }

    resources = {
        # Same sizing as the rancher VM in the suse-virt profile.
        "rancher":    {"memory_mib": 8192,  "vcpu": 4, "disk_gb": 60},
        # EIB VM: needs room for the EIB container + base OS images + built artifacts.
        "eib":        {"memory_mib": 12288, "vcpu": 4, "disk_gb": 100},
        # Edge nodes: minimum for Leap Micro 6.2 + Elemental agent.
        "edge-node":  {"memory_mib": 4096,  "vcpu": 2, "disk_gb": 20},
    }

    def extra_cfg(self) -> dict:
        return {
            "harvester_node_names": [],
            "rancher_tls": _RANCHER_TLS,
            "elemental": _ELEMENTAL,
        }

    # --- Success screen ---

    def success_extra_sections(self, cfg: dict) -> list[str]:
        edge_nodes = [(n, v) for n, v in cfg.get("vms", {}).items() if n.startswith("edge")]
        if not edge_nodes:
            return []
        lines = ["[bold]Edge node reference[/bold]  (static DHCP — MAC determines IP)"]
        lines.append("  node    MAC                  IP")
        for name, info in sorted(edge_nodes):
            mac = info.get("mac", "—")
            ip = info.get("ip", "—")
            lines.append(f"  {name:<7} {mac:<20} {ip}  (DHCP pre-assigned)")
        lines.append("")
        return lines

    def success_next_steps(self, cfg: dict) -> list[str]:
        return [
            "  rodeo ssh eib            # shell into the EIB VM (build Elemental OS images here)",
            "  rodeo ssh <host>/<vm>    # from laptop: hop via KVM/EC2 host",
            "  On the eib VM: edit /home/eib-config/edge-definition.yaml",
            "    → replace REPLACE_WITH_REGISTRATION_URL with the MachineRegistration URL",
            "    → run EIB to build the Elemental OS image (base OS from Hauler: http://localhost:8080)",
            "  From the KVM host: rodeo pull-edge-image   # seed edge1/2/3 boot disks",
            "  rodeo start edge1 edge2 edge3              # boot edge nodes into Elemental",
            "  In Rancher: Fleet → Git Repos → alien-geeko is waiting for edge clusters",
            "    → label your edge cluster: demo=true  edge-type=x86-cluster",
        ]


# Let's Encrypt via sslip.io — Rancher is accessed at rancher.<ext-ip>.sslip.io:443
# via Traefik ingress (no NodePort). Email is used for ACME registration.
# Override email in rodeo-plan.yaml under rancher_tls.email.
_RANCHER_TLS = {
    "source": "letsEncrypt",
    "email": "admin@example.com",
}

_ELEMENTAL = {
    # Number of MachineRegistration CRs to create in fleet-default.
    # Names are auto-generated: {registration_prefix}-reg-1 .. -N.
    "registrations": 1,
    # Prefix for generated MachineRegistration names. Defaults to the plan name
    # (set at init time in RancherPhase.__init__ from cfg["name"]).
    # Override here to use a fixed name regardless of the plan name.
    "registration_prefix": "",
}
