"""Translate a rodeo lab specification into a lab-in-a-box lab.json.

First concrete step of the lab-in-a-box deployer integration: rodeo stays the
source of truth (definition.yaml + rodeo-plan.yaml + profile defaults); this
module renders the same inventory that drives the native engine into the JSON
input consumed by lab-in-a-box's setup_lab.sh / destroy_lab.sh.

Targets lab-in-a-box main (release 1.0.0, commit b4b2a0a). Contract observed in
setup_lab.sh, setup_vm.sh, libs/lab_creation.bash and libs/k8s_functions.bash:

  nodes:      map keyed by VM name (an FQDN — used verbatim as the SSH and DNS
              name). Every key of a node object is exported as an env var when
              that VM is built, so per-node VM_MEM / VM_CPU / VM_DSK / NETWORK
              override the common defaults; 'kcluster' marks Kubernetes
              cluster membership; INSTALL_RKE2_TYPE picks the RKE2 role.
  common:     defaults exported for every VM (lab_name, mymask, mygw, mydns,
              mynet_reverse, mydomain, ISO_IMAGE, sizing, config_method).
  kclusters:  map keyed by cluster name; clu_type (k3s | rke2), clu_rel
              (release channel), mydomain, addons[] (each run once per cluster
              via install_<addon>).
  <addon>:    optional per-addon config section (exported by _load_vars).

Not representable in lab-in-a-box today (reported as warnings or errors):
  - PXE/iPXE-booted nodes (Harvester) — lab-in-a-box has no PXE support.
  - exposed_services host DNAT — lab-in-a-box does not manage the host firewall.
  - storage/image-dir selection — VM_IMG_LOC lives in /etc/lab_creation.cfg.
  - exact k8s version pins — lab-in-a-box installs from a release channel.
"""
from __future__ import annotations

import ipaddress
from typing import Any

from .config import ConfigError
from .inventory import build_inventory

# install_<addon> scripts shipped on lab-in-a-box main (release 1.0.0).
LIAB_ADDONS = frozenset({
    "argocd", "insecure_app", "jenkins", "longhorn", "mariadb", "neuvector",
    "nv-demo-helm", "nv_testing", "rancher", "struts_demo", "suma", "wordpress",
})

# Node flavors that form the management Kubernetes cluster in rodeo profiles.
_MGMT_FLAVORS = frozenset({"rancher"})


def _reverse_zone(cidr: str) -> str | None:
    """'192.168.122.0/24' -> '122.168.192' (bind reverse-zone name stem)."""
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return None
    if net.version != 4:
        return None
    octets = str(net.network_address).split(".")
    return ".".join(reversed(octets[:3]))


def _node_mgmt_mac(node: dict) -> str | None:
    if node.get("mgmt_mac"):
        return node["mgmt_mac"]
    for iface in node.get("interfaces", []):
        if iface.get("role") == "mgmt" and iface.get("mac"):
            return iface["mac"]
    return None


def _sizing(resources: dict, flavor: str) -> dict[str, str]:
    """Per-node VM_MEM/VM_CPU/VM_DSK from the plan's resources block.

    lab-in-a-box feeds VM_MEM to virt-install --memory (MiB) and VM_DSK to
    qemu-img resize as <n>G, so rodeo's memory_mib / disk_gb map 1:1. Values
    are emitted as strings to match the shell consumer.
    """
    spec = resources.get(flavor, {})
    out: dict[str, str] = {}
    if spec.get("memory_mib"):
        out["VM_MEM"] = str(spec["memory_mib"])
    if spec.get("vcpu"):
        out["VM_CPU"] = str(spec["vcpu"])
    if spec.get("disk_gb"):
        out["VM_DSK"] = str(spec["disk_gb"])
    return out


def build_lab_json(cfg: dict, *, skip_unsupported: bool = False) -> tuple[dict, list[str]]:
    """Render the loaded plan config into a lab-in-a-box lab definition.

    Returns (lab, warnings). Raises ConfigError when the topology contains
    nodes lab-in-a-box cannot deploy (PXE-booted Harvester nodes), unless
    skip_unsupported is set — then they are dropped and reported as warnings.
    """
    inv = build_inventory(cfg)
    overlay = cfg.get("lab_in_a_box") or {}
    resources = cfg.get("resources", {})
    versions = cfg.get("versions", {})
    warnings: list[str] = []

    net = inv.get("libvirt_network", {})
    plan_net = cfg.get("network", {})
    domain = net.get("domain") or plan_net.get("dns_domain") or "rodeo.lab"
    gateway = net.get("gateway") or plan_net.get("gateway")
    bridge = net.get("bridge", "virbr0")
    cidr = net.get("cidr")

    pxe_node_names = {n["name"] for n in inv.get("pxe", {}).get("nodes", [])}

    cluster_name = overlay.get("cluster_name", "mgmt")
    clu_type = overlay.get("cluster_type", "k3s")

    nodes: dict[str, dict[str, Any]] = {}
    cluster_members: list[str] = []
    unsupported: list[str] = []

    for node in inv.get("vm_nodes", []):
        name = node["name"]
        if name in pxe_node_names:
            unsupported.append(name)
            continue

        entry: dict[str, Any] = {}
        if node.get("ip"):
            entry["myip"] = node["ip"]
        mac = _node_mgmt_mac(node)
        if mac:
            entry["mymac"] = mac
            # setup_vm.sh only applies ${NETWORK:-bridge=br0,...}; a per-node
            # key wins over that default, so pin rodeo's bridge explicitly.
            entry["NETWORK"] = f"bridge={bridge},mac.address={mac}"
        else:
            entry["NETWORK"] = f"bridge={bridge}"

        flavor = node.get("flavor", "")
        sizing = _sizing(resources, flavor)
        if sizing:
            entry.update(sizing)
        else:
            warnings.append(
                f"node '{name}': no resources entry for flavor '{flavor}' — "
                "lab-in-a-box will fall back to its common VM_MEM/VM_CPU/VM_DSK"
            )

        if flavor in _MGMT_FLAVORS:
            entry["kcluster"] = cluster_name
            if clu_type == "rke2":
                entry["INSTALL_RKE2_TYPE"] = "server"
            cluster_members.append(name)

        nodes[f"{name}.{domain}"] = entry

    if unsupported and not skip_unsupported:
        raise ConfigError(
            f"Nodes not deployable by lab-in-a-box (PXE/iPXE boot): {', '.join(sorted(unsupported))}\n"
            "lab-in-a-box has no PXE support — Harvester labs stay on the native engine.\n"
            "Use --skip-unsupported to export only the non-PXE nodes, or a non-PXE "
            "profile such as 'rancher'."
        )
    if unsupported:
        warnings.append(
            f"skipped PXE-booted node(s) not deployable by lab-in-a-box: {', '.join(sorted(unsupported))}"
        )

    common: dict[str, Any] = {"lab_name": cfg.get("name", "rodeo")}
    if cidr:
        common["mymask"] = str(ipaddress.ip_network(cidr, strict=False).prefixlen)
        reverse = _reverse_zone(cidr)
        if reverse:
            common["mynet_reverse"] = reverse
    if gateway:
        common["mygw"] = gateway
        # In rodeo's NAT network libvirt's dnsmasq answers DNS on the gateway.
        common["mydns"] = gateway
    common["mydomain"] = domain
    # rodeo guests are cloud-init provisioned (lab-in-a-box's default empty
    # config_method means ignition/combustion, which fits SLE Micro images).
    common["config_method"] = overlay.get("config_method", "cloud-init")
    if overlay.get("iso_image"):
        common["ISO_IMAGE"] = overlay["iso_image"]
    else:
        warnings.append(
            "no base image set — lab-in-a-box needs common.ISO_IMAGE (a qcow2 in its "
            "ISO_LOC); set lab_in_a_box.iso_image in rodeo-plan.yaml or pass "
            "-P lab_in_a_box.iso_image=<name>"
        )

    lab: dict[str, Any] = {"nodes": nodes, "common": common}

    if cluster_members:
        if overlay.get("addons") is not None:
            addons = list(overlay["addons"])
        else:
            addons = [
                c["name"] for c in inv.get("components", [])
                if not c.get("on_host") and c.get("name") in LIAB_ADDONS
            ]
        kcluster: dict[str, Any] = {
            "clu_type": clu_type,
            "clu_rel": overlay.get("clu_rel", "stable"),
            "mydomain": domain,
        }
        if addons:
            kcluster["addons"] = addons
        lab["kclusters"] = {cluster_name: kcluster}

        pinned = versions.get(clu_type)
        if pinned:
            warnings.append(
                f"{clu_type} version pin '{pinned}' is not portable — lab-in-a-box "
                f"installs from a release channel (clu_rel: {kcluster['clu_rel']})"
            )

        if "rancher" in addons:
            rancher_section: dict[str, Any] = {"rancher_shorthn": "rancher"}
            if versions.get("rancher"):
                rancher_section["rancher_version"] = versions["rancher"]
            if versions.get("cert_manager"):
                rancher_section["cert_manager_ver"] = f"--version {versions['cert_manager']}"
            lab["rancher"] = rancher_section

    if inv.get("firewall", {}).get("port_forwards"):
        warnings.append(
            "exposed_services host port-forwards are not managed by lab-in-a-box — "
            "configure host DNAT separately (rodeo's kvm_host firewall phase or manually)"
        )

    # Escape hatch: verbatim extra/override sections for lab-in-a-box
    # (e.g. per-addon config blocks the translator doesn't know about).
    for key, value in (overlay.get("sections") or {}).items():
        if isinstance(value, dict) and isinstance(lab.get(key), dict):
            lab[key] = {**lab[key], **value}
        else:
            lab[key] = value

    return lab, warnings
