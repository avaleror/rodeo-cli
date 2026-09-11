"""Translate a rodeo lab specification into a lab-in-a-box lab.json.

rodeo stays the source of truth (definition.yaml + rodeo-plan.yaml + profile
defaults); this module renders the same inventory that drives the native
engine into the JSON input consumed by lab-in-a-box's setup_lab.py /
destroy_lab.py. It backs both `rodeo export` and the `lab-in-a-box` deploy
engine (rodeo/engine/labinabox_runner.py).

Targets lab-in-a-box release 1.8.0 — the Python-based contract introduced by
the 1.5.0 rewrite. Authoritative schema: `setup_lab.py --input-definition
json` (scripts/lab_schema, base_lab_schema()):

  nodes:      map keyed by VM name (an FQDN — used verbatim as the SSH and DNS
              name). Node keys overlay the common defaults per VM (VM_MEM /
              VM_CPU / VM_DSK, myip, mymac, forwarded_ports …); 'kcluster'
              marks Kubernetes cluster membership; INSTALL_RKE2_TYPE picks the
              RKE2 role. NETWORK is no longer a lab-file key — the libvirt
              network string is built server-side from BRIDGE in
              lab_creation.cfg; explicit mymac values also avoid its
              interactive MAC-conflict prompt.
  common:     lab-wide defaults (lab_name, mymask, mygw, mydns, mynet_reverse,
              mydomain, ISO_IMAGE, sizing, config_method, services[]).
  kclusters:  map keyed by cluster name; clu_type (k3s | rke2), clu_rel
              (release channel), mydomain, addons[] (each run once per cluster
              via install_<addon>; an entry may also be a single-key
              {"<addon>": {...}} config-override mapping since 1.8.0).
  <addon>:    optional per-addon config section (see install_<addon> --schema).

Still not representable (reported as warnings or errors):
  - PXE/iPXE-booted nodes (Harvester) — lab-in-a-box grew a generic PXE
    service, but rodeo's Harvester labs stay on the native engine's live-
    validated boot chain until that path is regression-tested (ROADMAP).
  - storage/image-dir selection — VM_IMG_LOC/ISO_LOC live in
    /etc/lab_creation.cfg on the automation VM, not in the lab file.
  - exact k8s version pins — lab-in-a-box installs from a release channel.
"""
from __future__ import annotations

import ipaddress
from typing import Any

from .config import ConfigError
from .inventory import build_inventory

# install_<addon> scripts shipped with lab-in-a-box release 1.8.0. Used when
# deriving addons from rodeo components; overlay-specified addons always win
# (upstream's own preflight validates them against what is installed there).
LIAB_ADDONS = frozenset({
    "agones", "anthropic", "apertus", "appcollection", "argocd",
    "client_registration", "codellama", "colt", "complianceascode", "coredns",
    "deepseek", "ds389", "fluentd", "fluid", "gemini", "gpu_operator",
    "harbor", "harvester", "home_assistant", "insecure_app", "istio",
    "jenkins", "kagent", "keycloak", "kimi", "kiwi", "kubewarden", "kucero",
    "linkerd", "longhorn", "mailman", "mariadb", "mediagoblin", "milvus",
    "mistral", "neuvector", "nginx", "nv-demo-helm", "nv_testing", "ollama",
    "open_saber", "open_webui", "openai", "openldap", "phoebe", "postgresql",
    "qdrant", "qwen", "rancher", "skynet_simulator", "smlm", "smlm_proxy",
    "stackpack", "starcoder2", "struts_demo", "suma", "supertux_classic",
    "suse_ai", "suse_observability", "traefik", "trento", "uyuni", "weaviate",
    "wikimusic", "wordpress",
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
            # Explicit MACs keep DNS/DHCP deterministic and avoid lab-in-a-box's
            # interactive MAC-conflict prompt (it reads the answer from a TTY).
            entry["mymac"] = mac
        # The libvirt network string is built by lab-in-a-box from BRIDGE in
        # lab_creation.cfg (per-node NETWORK stopped being a lab-file key in
        # the Python rewrite) — the automation VM's config selects the bridge.

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
            f"Nodes not translated for lab-in-a-box (PXE/iPXE boot): {', '.join(sorted(unsupported))}\n"
            "Harvester labs stay on the native engine until lab-in-a-box's PXE "
            "path is live-regression-tested (see ROADMAP).\n"
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

    # exposed_services host DNAT → lab-in-a-box's portforward service:
    # per-node forwarded_ports ("<ext>:<int>/<PROTO>") applied on the
    # hypervisor. Forwards whose target is not a node address (e.g. a floating
    # VIP) cannot be attributed to a node and are reported instead.
    ip_to_fqdn = {
        entry["myip"]: fqdn for fqdn, entry in nodes.items() if entry.get("myip")
    }
    for fwd in inv.get("firewall", {}).get("port_forwards", []):
        fqdn = ip_to_fqdn.get(fwd.get("toaddr"))
        rule = f"{fwd['port']}:{fwd['toport']}/{str(fwd.get('proto', 'tcp')).upper()}"
        if fqdn is None:
            warnings.append(
                f"port-forward {rule} → {fwd.get('toaddr')} targets no exported "
                "node (VIP?) — not representable as a lab-in-a-box forwarded_port"
            )
            continue
        nodes[fqdn].setdefault("forwarded_ports", []).append(rule)
    if any("forwarded_ports" in entry for entry in nodes.values()):
        common.setdefault("services", []).append("portforward")

    lab: dict[str, Any] = {"nodes": nodes, "common": common}

    if cluster_members:
        if overlay.get("addons") is not None:
            addons = list(overlay["addons"])
            # 1.8.0 allows {"<addon>": {...}} override entries — validate names.
            names = [next(iter(a)) if isinstance(a, dict) else a for a in addons]
            unknown = sorted(set(names) - LIAB_ADDONS)
            if unknown:
                warnings.append(
                    f"addon(s) not shipped with lab-in-a-box 1.8.0: {', '.join(unknown)} "
                    "— its preflight will fail unless install_<addon> exists on the "
                    "automation VM"
                )
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

    # Escape hatch: verbatim extra/override sections for lab-in-a-box
    # (e.g. per-addon config blocks the translator doesn't know about).
    for key, value in (overlay.get("sections") or {}).items():
        if isinstance(value, dict) and isinstance(lab.get(key), dict):
            lab[key] = {**lab[key], **value}
        else:
            lab[key] = value

    return lab, warnings
