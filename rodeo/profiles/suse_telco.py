"""SUSE Telco Cloud 3.7 profile: management cluster and two downstream hosts.

The phase list is the order `rodeo plan` shows. This slice does not build
images, install Metal3, or create libvirt guests. kvm_host, images, vms, and
enroll stay out of the phase cache so a later implementation is not skipped
as already done.

Versions are the SUSE Telco Cloud 3.7.0 release notes, component table
(documentation.suse.com/suse-telco/3.7, Appendix "Release Notes"). Helm-installed
components carry the chart version, because that is what `helm --version` takes.
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
        "mgmt": {"ip": "192.168.122.10", "user": "root"},
        "site-co": {"ip": "192.168.122.21", "user": "root"},
        "site-ran": {"ip": "192.168.122.32", "user": "root"},
    }

    # Management cluster is RKE2. The inherited k3s key is unused here.
    versions = {
        **BASE_VERSIONS,
        "suse_telco_cloud": "3.7.0",
        "rancher": "2.15.1",
        "rke2": "v1.36.3+rke2r1",
        "cert_manager": "v1.20.1",
        # Turtles itself ships inside Rancher (on by default since 2.13; image
        # v0.27.1). This is the providers chart: CAPI v1.13.3, CAPM3 v1.13.2,
        # RKE2 bootstrap and control plane v0.25.0.
        "turtles_providers": "307.0.8+up0.27.0",
        # Metal3 0.16.0: baremetal-operator 0.13.3, Ironic 38.0.0, IPA 3.0.10.
        "metal3": "307.0.31+up0.16.0",
        "metallb": "307.0.3+up0.16.1",
        "endpoint_copier_operator": "307.0.1+up0.3.0",
        "longhorn": "1.12.1",
        # KubeVirt 1.8.3, CDI 1.65.0, dashboard extension 1.4.2 (lab 07 only).
        "kubevirt": "307.0.3+up0.8.0",
        "cdi": "307.0.3+up0.8.0",
        "kubevirt_dashboard_extension": "307.0.5+up1.4.2",
        "eib": "1.3.4",
        "kiwi_builder": "10.2.29.1",
        "telco_examples_ref": "e4ec0b0a7349a635c1c5efd94bdefeaedbfa847c",
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
                "gateway": "192.168.122.1",
                "dns_domain": "northline.telco",
                "vip": "",
                "rancher_ip": "192.168.122.10",
            },
            "telco": _TELCO,
            "alien_geeko": _ALIEN_GEEKO,
        }

    def success_next_steps(self, cfg: dict) -> list[str]:
        return [
            "  rodeo plan                 # mgmt, site-co, site-ran on 192.168.122.0/24",
            "  This slice does not boot VMs or install Metal3 yet.",
        ]


# Rancher's own self-signed cert on a NodePort. No public 80/443.
_RANCHER_TLS = {
    "source": "rancher",
}

# What the enroll and images phases build, matched to the workshop manifests.
# Labels are the hostSelector values in manifests/downstream-single.yaml and
# manifests/site-ran.yaml. site-ran's host lives in namespace northline because
# its Metal3MachineTemplate does, so enroll creates that namespace first.
_TELCO = {
    "bmc": {
        # sushy-tools on the KVM host, libvirt driver, Redfish virtual media.
        "address": "192.168.122.1",
        "port": 8000,
    },
    "image_cache": {
        "hostname": "imagecache.local",
        "port": 8080,
        # One downstream image for both sites, built by EIB from SL Micro 6.2 Base.
        "image_name": "eibimage-downstream-cluster.raw",
    },
    "sites": {
        "site-co": {
            "namespace": "default",
            "cluster": "site-co-01",
            "control_plane_endpoint": "192.168.122.21",
            "labels": {"cluster-role": "control-plane", "site": "site-co"},
        },
        "site-ran": {
            "namespace": "northline",
            "cluster": "site-ran-01",
            "control_plane_endpoint": "192.168.122.22",
            "labels": {
                "cluster-role": "control-plane",
                "deploy-region": "northline",
                "cluster-type": "site-ran",
            },
        },
    },
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
