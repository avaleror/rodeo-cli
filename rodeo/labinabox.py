"""Translate a rodeo lab specification into a lab-in-a-box lab.json.

rodeo stays the source of truth (definition.yaml + rodeo-plan.yaml + profile
defaults); this module renders the same inventory that drives the native
engine into the JSON input consumed by lab-in-a-box's setup_lab.py /
destroy_lab.py — used by `rodeo export` and by the lab-in-a-box platform
(rodeo/profiles/labinabox.py), which runs setup_lab.py itself.

Contract (lab-in-a-box's Python orchestration — setup_lab.py, setup_vm.py,
libs/lab_creation.py, libs/k8s.py; `setup_lab.py --schema json` prints it):

  nodes:      map keyed by VM name (an FQDN — used verbatim as the SSH and DNS
              name). Every key of a node object is exported as an env var when
              that VM is built, so per-node VM_MEM / VM_CPU / VM_DSK / BRIDGE /
              ISO_IMAGE / config_method / VM_BOOT override the common defaults;
              'kcluster' marks Kubernetes cluster membership; INSTALL_RKE2_TYPE
              picks the RKE2 role; 'addons' lists node-level addons (install_<addon>
              run against that VM, e.g. smlm in podman mode).
  common:     defaults exported for every VM (lab_name, mymask, mygw, mydns,
              mynet_reverse, mydomain, ISO_IMAGE, sizing, config_method).
  kclusters:  map keyed by cluster name; clu_type (k3s | rke2), clu_rel
              (release channel), mydomain, addons[] (each run once per cluster
              via install_<addon>).
  <addon>:    optional per-addon config section (exported by _load_vars).

Per-node knobs rodeo's definition has no field for (ISO_IMAGE, config_method,
VM_BOOT, VM_DSK_BUS, VM_NET_MODEL, addons) come from the plan's
lab_in_a_box.nodes.<short name> block.

Not representable in lab-in-a-box today (reported as warnings or errors):
  - PXE/iPXE-booted nodes (Harvester) — lab-in-a-box has no PXE support.
  - exposed_services host DNAT — lab-in-a-box does not manage the host firewall.
  - storage/image-dir selection — VM_IMG_LOC lives in /etc/lab_creation.defaults.
  - exact k8s version pins — lab-in-a-box installs from a release channel.
"""
from __future__ import annotations

import copy
import ipaddress
import re
from pathlib import Path
from typing import Any

from .config import ConfigError
from .inventory import build_inventory

# install_<addon> scripts rodeo can infer from a definition's components when
# the plan doesn't list addons explicitly. A checkout's full list comes from
# available_addons().
LIAB_ADDONS = frozenset({
    "argocd", "insecure_app", "jenkins", "longhorn", "mariadb", "neuvector",
    "nv-demo-helm", "nv_testing", "rancher", "struts_demo", "suma", "wordpress",
})

# Node flavors that form the management Kubernetes cluster in rodeo profiles.
_MGMT_FLAVORS = frozenset({"rancher"})


def available_addons(checkout: Path) -> frozenset[str]:
    """Addon names a lab-in-a-box checkout provides (scripts/install_<name>[.py])."""
    return frozenset(
        p.name.removeprefix("install_").removesuffix(".py")
        for p in (checkout / "scripts").glob("install_*")
        if p.is_file()
    )


def unresolved_placeholders(obj: Any, path: str = "") -> list[str]:
    """Dotted paths of every string still holding a ??secret placeholder."""
    if isinstance(obj, str):
        return [path] if obj.startswith("??") else []
    if isinstance(obj, dict):
        return [p for k, v in obj.items() for p in unresolved_placeholders(v, f"{path}.{k}" if path else str(k))]
    if isinstance(obj, list):
        return [p for i, v in enumerate(obj) for p in unresolved_placeholders(v, f"{path}[{i}]")]
    return []


def _merge(base: Any, over: Any) -> Any:
    """Deep-merge a variant overlay: dicts merge, None deletes a key, the rest replaces."""
    if not (isinstance(base, dict) and isinstance(over, dict)):
        return copy.deepcopy(over)
    out = copy.deepcopy(base)
    for key, value in over.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = _merge(out.get(key), value)
    return out


def _merge_images(base: list[dict], over: list[dict]) -> list[dict]:
    """Variant images merge by name; an entry with `remove: true` drops that image."""
    merged = {img.get("name"): copy.deepcopy(img) for img in base}
    for img in over:
        if img.get("remove"):
            merged.pop(img.get("name"), None)
        else:
            merged[img.get("name")] = _merge(merged.get(img.get("name"), {}), img)
    return list(merged.values())


def _without_unused_images(overlay: dict) -> dict:
    """Cloud VMs boot provider images: the local `images` list doesn't apply (and
    its ??secrets — qcow2 URLs — mustn't be asked for)."""
    if overlay.get("cloud"):
        overlay.pop("images", None)
    return overlay


def effective_overlay(cfg: dict) -> dict:
    """The plan's lab_in_a_box block with its selected variant applied.

    lab_in_a_box.variants.<name> is an overlay (see _merge / _merge_images);
    lab_in_a_box.variant picks one (e.g. -P lab_in_a_box.variant=scratch).
    A variant may also carry `operator_secrets` (added to the plan's) and a
    `notice` logged at deploy — neither ends up in lab.json.
    """
    overlay = copy.deepcopy(cfg.get("lab_in_a_box") or {})
    variants = overlay.pop("variants", None) or {}
    name = overlay.get("variant")
    if not name:
        return _without_unused_images(overlay)
    if name not in variants:
        raise ConfigError(f"lab_in_a_box.variant '{name}' is not one of: {', '.join(variants) or '(none)'}")
    variant = dict(variants[name] or {})
    for key in ("operator_secrets", "notice"):
        variant.pop(key, None)
    images = variant.pop("images", None)
    merged = _merge(overlay, variant)
    if images is not None:
        merged["images"] = _merge_images(overlay.get("images") or [], images)
    return _without_unused_images(merged)


def variant_extras(cfg: dict) -> dict:
    """{operator_secrets, notice} of the selected variant (empty when none)."""
    overlay = cfg.get("lab_in_a_box") or {}
    variant = (overlay.get("variants") or {}).get(overlay.get("variant") or "") or {}
    return {"operator_secrets": list(variant.get("operator_secrets") or []), "notice": variant.get("notice")}


def apply_variant(plan: dict) -> dict:
    """A plan with its selected variant applied — what secrets are really referenced."""
    out = copy.deepcopy(plan)
    if "lab_in_a_box" in out:
        out["lab_in_a_box"] = effective_overlay(plan)
        out["operator_secrets"] = list(plan.get("operator_secrets") or []) + variant_extras(plan)["operator_secrets"]
    return out


def unknown_fields(lab: dict, schema: dict) -> list[str]:
    """lab.json fields a lab-in-a-box schema doesn't declare (dotted paths).

    `schema` is {"common": [...], "nodes": [...], "kclusters": [...],
    "addons": {name: [...]}} — the installed lab-in-a-box's own, or the test
    snapshot. An addon section/override for an addon the schema lacks is
    reported as the addon itself.
    """
    addons = schema.get("addons", {})
    unknown = [f"common.{k}" for k in lab.get("common", {}) if k not in schema["common"]]
    for name, node in lab.get("nodes", {}).items():
        unknown += [f"nodes.{name}.{k}" for k in node if k not in schema["nodes"]]
        for entry in node.get("addons", []):
            for addon, overrides in (entry.items() if isinstance(entry, dict) else [(entry, {})]):
                if addon not in addons:
                    unknown.append(f"nodes.{name}.addons.{addon}")
                    continue
                unknown += [f"nodes.{name}.addons.{addon}.{k}" for k in overrides or {}
                            if k not in addons[addon]]
    for name, cluster in lab.get("kclusters", {}).items():
        unknown += [f"kclusters.{name}.{k}" for k in cluster if k not in schema.get("kclusters", [])]
    for key, section in lab.items():
        if key in ("nodes", "common", "kclusters"):
            continue
        if key not in addons:
            unknown.append(key)
        elif isinstance(section, dict):
            unknown += [f"{key}.{k}" for k in section if k not in addons[key]]
    return unknown


_NODE_REF_RE = re.compile(r"\$\{node_(fqdn|ip):([A-Za-z0-9][A-Za-z0-9_-]*)\}")


def _substitute_node_refs(obj: Any, nodes: dict[str, dict]) -> Any:
    """Fill ${node_fqdn:<vm>} / ${node_ip:<vm>} in any lab.json string.

    Lets a plan point an addon at "this lab's <vm>" — which differs per lab
    instance (rodeo/instances.py) — instead of a hardcoded name or address.
    """
    by_short = {fqdn.split(".")[0]: (fqdn, node.get("myip", "")) for fqdn, node in nodes.items()}

    def fill(match: re.Match) -> str:
        kind, name = match.groups()
        if name not in by_short:
            raise ConfigError(f"${{node_{kind}:{name}}}: no lab node named '{name}'")
        return by_short[name][0 if kind == "fqdn" else 1]

    if isinstance(obj, str):
        return _NODE_REF_RE.sub(fill, obj)
    if isinstance(obj, dict):
        return {k: _substitute_node_refs(v, nodes) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_node_refs(v, nodes) for v in obj]
    return obj


def _open_cloud_ports(cloudtype: str, nodes: dict[str, dict], services: list[dict]) -> list[str]:
    """exposed_services on cloud VMs: each is reached on the VM's own address, so the
    guest port goes into that node's open_ports — lab-in-a-box opens it with the
    provider's own mechanism (security group, firewall rule, ...; see its README)."""
    by_short = {fqdn.split(".")[0]: node for fqdn, node in nodes.items()}
    warnings = []
    for svc in services:
        node = by_short.get(str(svc.get("target", "")))
        if node is None:
            warnings.append(f"exposed_services '{svc.get('name', '?')}': no lab node '{svc.get('target')}'")
            continue
        proto = svc.get("proto", "tcp")
        entry = str(svc["guest_port"]) + ("" if proto == "tcp" else f"/{proto}")
        ports = node.setdefault("open_ports", [])
        if entry not in ports:
            ports.append(entry)
    return warnings


def _image_source(images: list[dict], name: str | None) -> dict[str, str]:
    """ISO_URL / ISO_SHA256 / ISO_SHA256_URL for base image `name`, if the plan lists it."""
    for image in images:
        if name and image.get("name") == name:
            url = image.get("url")
            fields = {"ISO_URL": " ".join(url) if isinstance(url, list) else url,
                      "ISO_SHA256": image.get("sha256"),
                      "ISO_SHA256_URL": image.get("sha256_url")}
            return {k: str(v) for k, v in fields.items() if v}
    return {}


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
    overlay = effective_overlay(cfg)
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

    node_overrides: dict[str, dict] = overlay.get("nodes") or {}
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
        # setup_vm.py attaches the NIC to ${BRIDGE:-br0}; pin rodeo's bridge.
        entry["BRIDGE"] = bridge

        flavor = node.get("flavor", "")
        sizing = _sizing(resources, flavor)
        if sizing:
            entry.update(sizing)
        else:
            warnings.append(
                f"node '{name}': no resources entry for flavor '{flavor}' — "
                "lab-in-a-box will fall back to its common VM_MEM/VM_CPU/VM_DSK"
            )

        entry.update(node_overrides.get(name) or {})

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
    # lab-in-a-box requires common VM_MEM / VM_CPU / VM_DSK; node values override
    # them, so common carries the largest of each.
    for key in ("VM_MEM", "VM_CPU", "VM_DSK"):
        values = [int(n[key]) for n in nodes.values() if str(n.get(key, "")).isdigit()]
        if values:
            common[key] = str(max(values))
    # rodeo guests are cloud-init provisioned (lab-in-a-box's default empty
    # config_method means ignition/combustion, which fits SLE Micro images).
    common["config_method"] = overlay.get("config_method", "cloud-init")
    if overlay.get("iso_image"):
        common["ISO_IMAGE"] = overlay["iso_image"]
    elif not nodes or not all(n.get("ISO_IMAGE") for n in nodes.values()):
        warnings.append(
            "no base image set — lab-in-a-box needs common.ISO_IMAGE (a qcow2 in its "
            "ISO_LOC); set lab_in_a_box.iso_image in rodeo-plan.yaml or pass "
            "-P lab_in_a_box.iso_image=<name>"
        )

    if overlay.get("root_password"):
        # Root password of every VM (plain for virt_customize; the lab-in-a-box
        # phase adds the ROOT_PWD_HASH cloud-init/Ignition nodes need).
        common["VM_ROOT_PASS"] = overlay["root_password"]

    from .labinabox_host import cloud as _cloud

    cloud_spec = _cloud(cfg)
    if cloud_spec:
        # Cloud VMs: the provider assigns addresses and networks; ISO_IMAGE is
        # a provider image (AMI, ...) and nothing is downloaded locally.
        for entry in nodes.values():
            for key in ("myip", "mymac", "BRIDGE"):
                entry.pop(key, None)
        for key in ("mygw", "mydns", "mymask"):
            common.pop(key, None)
        common["cloud_account"] = cloud_spec["account"]
        warnings += _open_cloud_ports(cloud_spec["cloudtype"], nodes,
                                      (inv.get("_raw_topology") or {}).get("exposed_services") or [])
    else:
        # Base images lab-in-a-box downloads itself (setup_lab.py fetches ISO_URL onto
        # each KVM host, verified by ISO_SHA256 / ISO_SHA256_URL) — attached to every
        # node, or common, whose ISO_IMAGE is one of the plan's lab_in_a_box.images.
        for holder in [common, *nodes.values()]:
            source = _image_source((overlay.get("images") or []), holder.get("ISO_IMAGE"))
            if source:
                holder.update(source)

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

    lab = _substitute_node_refs(lab, nodes)
    return lab, warnings
