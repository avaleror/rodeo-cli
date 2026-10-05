"""SUSE Telco Cloud 3.7 profile — management cluster and two downstream hosts.

The phase list is the order `rodeo plan` shows. This slice does not build
images, install Metal3, or create libvirt guests. kvm_host, images, vms, and
enroll stay out of the phase cache so a later implementation is not skipped
as already done.
"""
from __future__ import annotations

from .base import BASE_VERSIONS, RodeoProfile


class SuseTelcoProfile(RodeoProfile):
    name = "suse-telco"
    phases = [
        "kvm_host",
        "images",
        "vms",
        "boot",
        "enroll",
        "finalise",
        "custom_scripts",
    ]
    vm_names = ["mgmt", "site-co", "site-ran"]
    ansible_phases = frozenset()
    guarded_phases = frozenset(["finalise", "custom_scripts"])
    no_cache_phases = frozenset(["kvm_host", "images", "vms", "enroll", "custom_scripts"])

    static_vms = {
        "mgmt": {"ip": "192.168.125.10", "user": "root"},
        "site-co": {"ip": "192.168.125.21", "user": "root"},
        "site-ran": {"ip": "192.168.125.22", "user": "root"},
    }

    # Management cluster is RKE2. The inherited k3s key is unused here.
    versions = {
        **BASE_VERSIONS,
        "rancher": "2.15.1",
        "rke2": "v1.36.3+rke2r1",
        "cert_manager": "v1.20.1",
        "turtles": "307.0.8+up0.27.0",
        "metal3": "307.0.31+up0.16.0",
    }

    # CPU and RAM are the workshop starting sizes. Disk is a placeholder
    # until the first image boot, not a measured pin.
    resources = {
        "mgmt": {"memory_mib": 16384, "vcpu": 8, "disk_gb": 80},
        "site": {"memory_mib": 8192, "vcpu": 4, "disk_gb": 40},
    }

    def extra_cfg(self) -> dict:
        return {
            "harvester_node_names": [],
            "rancher_tls": _RANCHER_TLS,
            "network": {
                "mode": "nat",
                "gateway": "192.168.125.1",
                "dns_domain": "northline.telco",
                "vip": "",
                "rancher_ip": "192.168.125.10",
            },
            "alien_geeko": _ALIEN_GEEKO,
        }

    def success_next_steps(self, cfg: dict) -> list[str]:
        return [
            "  rodeo plan                 # mgmt, site-co, site-ran on 192.168.125.0/24",
            "  This slice does not boot VMs or install Metal3 yet.",
        ]


# NodePort plus a rodeo-managed cert. No public 80/443.
_RANCHER_TLS = {
    "source": "secret",
}

_ALIEN_GEEKO = {
    "image": "docker.io/avaleror/telco-site-console:latest",
    "fleet_repo": "https://github.com/avaleror/telco-site-console.git",
    "fleet_branch": "main",
    "fleet_name": "telco-site-console",
    "fleet_namespace": "fleet-default",
    "target_labels": {
        "demo": "true",
        "site-type": "downstream",
    },
}
