"""Topology / inventory definition loader and renderer.

This module (and the accompanying definition file under rodeo/data/profiles/suse-virt/definition.yaml)
is the EXAMPLE of how to remove the previous hardcoded topology assumptions for the Harvester/SUSE Virtualization rodeo.

Current status:
- The single source of truth for the Harvester rodeo lives in rodeo/data/profiles/suse-virt/definition.yaml (the file you edit to define "what a Harvester rodeo is").
- The renderer here compiles the logical model into concrete artifacts (vm_nodes with generated or explicit MACs, interfaces/cables, pxe data, firewall rules, storage, libvirt network, host_prep, etc.).
- Full wiring to all consumers and removal of remaining hardcodes is ongoing per the action items.

See rodeo/data/profiles/suse-virt/definition.yaml for extensive comments on the expected structure for the Harvester rodeo.
Host prep (sysctls, selinux, ovmf, network expectations) added in Phase 1 of EIB plan.
"""

from __future__ import annotations

from pathlib import Path

import hashlib
import uuid

import yaml

from .config_dir import load_config_dir

# Base location for profile definition files (packaged with the wheel)
_DATA_ROOT = Path(__file__).parent / "data" / "profiles"


def _load_topology(profile_name: str, config_dir: str | Path | None = None) -> dict:
    """Load the raw topology definition for a profile (e.g. 'suse-virt').

    If config_dir is given and <config_dir>/definition.yaml exists, it is used
    (allows custom definitions + EIB-style artifacts dir for a lab).
    Falls back to the bundled profile definition otherwise.
    """
    if config_dir:
        candidate = Path(config_dir) / "definition.yaml"
        if candidate.exists():
            path = candidate
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            if "definition" not in data and "topology" not in data:
                # allow raw top-level or wrapped; be permissive for user defs
                pass
            return data.get("definition") or data.get("topology") or data

    path = _DATA_ROOT / profile_name / "definition.yaml"
    if not path.exists():
        # Fallback for development when running directly from source tree
        # (the package layout puts data/ next to inventory.py)
        path = Path(__file__).parent / "data" / "profiles" / profile_name / "definition.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"No definition file found for profile '{profile_name}'. "
            f"Expected at {path} (or packaged data equivalent). "
            "See rodeo/data/profiles/suse-virt/definition.yaml as the canonical example for the Harvester rodeo."
        )
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if "definition" not in data:
        raise ValueError(f"definition.yaml for {profile_name} must contain a top-level 'definition:' key (or the legacy 'topology:' key for transition)")
    # Support legacy 'topology:' key during transition
    return data.get("definition") or data.get("topology")


def build_inventory(cfg: dict) -> dict:
    """Render the final inventory from the definition + any plan overrides in cfg.

    This is the function that will eventually be called from:
      - DeployRunner._write_vars_file (to emit vm_nodes + libvirt_flavors into Ansible)
      - ClusterPhase.__init__ / stream (for start_order, harvester_node_names, ready_count, etcd gap)
      - RancherPhase (for harvester_nodes list)
      - plan_cmd (for flavor lookup)
      - validate_config (for richer consistency checks)
      - profiles (so default_cfg can be derived from the definition)

    Supports --config-dir (Phase 2): if cfg["config_dir"] points to a dir with definition.yaml,
    that definition is loaded instead of (or before) the bundled profile one. The dir contents
    (certs, manifests, scripts, ...) are recorded under _config_dir for consumption by build
    / renderer phases (EIB-style artifacts embedding).

    Current implementation: returns rendered + compiled from definition (plus plan overrides via cfg).
    Focus: Harvester/SUSE Virtualization rodeo (our current actual focus).

    The key insight:
    - Do **not** put raw firewall rules, raw iPXE scripts, or raw libvirt XML into the YAML definition.
    - Declare the **logical model** (nodes + their interface roles from templates + exposed services + boot policy).
    - The renderer (this file) compiles the logical model into all the concrete "cables"
      (MACs, interfaces, DHCP, iPXE data, firewall/NAT rules, etc.) that libvirt, firewalld, nginx,
      Harvester configs, and cloud-init expect.
    - Generation of MACs/hostnames/UUIDs happens on the fly *unless* explicitly in the definition file.
    """
    profile_name = cfg.get("type", "suse-virt")
    plan_name = cfg.get("name", profile_name)  # used for deterministic generation
    config_dir = cfg.get("config_dir")
    topology = _load_topology(profile_name, config_dir)

    raw_nodes = topology.get("nodes", [])
    node_templates = topology.get("node_templates", {})

    # Core renderer step: for each node in the definition, generate MACs, hostnames,
    # UUIDs, etc. *unless* they are explicitly provided in the YAML.
    # This allows the definition file for the Harvester rodeo to stay high-level.
    nodes = []
    for idx, raw_node in enumerate(raw_nodes):
        rendered = _render_node(raw_node, node_templates, plan_name, idx)
        normalized = _normalize_node(rendered, topology)
        nodes.append(normalized)

    # Example "compiler" functions that turn the high-level definition
    # into the low-level data structures the roles and Python phases currently consume.
    # In a complete implementation these would be called here and their output
    # put into the returned dict (and emitted to the Ansible vars file).

    return {
        "vm_nodes": nodes,                    # normalized to have both modern interfaces + legacy flat macs
        "start_order": topology.get("start_order", []),
        "harvester_node_names": topology.get("harvester_node_names", []),
        "harvester_ready_count": topology.get("harvester_ready_count", 3),
        "etcd_join_gap_seconds": topology.get("etcd_join_gap_seconds", 90),

        # === Compiled "cables" examples (what you asked for) ===
        # These are what inventory.py would produce from the logical model above.

        # 1. Concrete data for the pxe_server role (iPXE + nginx + dnsmasq extensions)
        "pxe": _compile_pxe_data(topology, nodes),

        # 2. Concrete NAT / DNAT / firewall rules for kvm_host role
        "firewall": _compile_firewall_rules(topology, nodes),

        # 3. The extended libvirt network definition (the "cables" + DHCP + TFTP/HTTP options)
        "libvirt_network": _compile_libvirt_network(topology, nodes),

        # Storage config from definition (for multi-disk hosts, image_dir, pool, etc.)
        # This is now alongside the other components (network, boot, exposed_services, node_templates).
        # The renderer passes it through; it gets emitted to Ansible vars and can be used
        # by storage setup tasks to select/format the right disk.
        "storage": topology.get("storage", {}),

        # High-level components (for documentation, "rodeo describe", and future features)
        "components": topology.get("components", []),

        # Harvester and Rancher specific recipe data from the definition (the "what" for the ISOs and cloud-init).
        "harvester": topology.get("harvester", {}),
        "rancher": topology.get("rancher", {}),

        # Host prep expectations (sysctls, selinux, ovmf, network rules) declared in definition for the Harvester recipe.
        # Passed through so runner can emit vars for kvm_host / vms roles. See Phase 1 of the EIB plan.
        "host_prep": _compile_host_prep(topology),

        # Config dir info (EIB-style artifacts dir). Recorded for use by build/render phases.
        # If dir had definition.yaml it was already preferred in _load_topology.
        "_config_dir": load_config_dir(config_dir) if config_dir else None,

        # Raw definition always available for advanced use or new renderers
        "_raw_topology": topology,
    }


def _normalize_node(node: dict, topology: dict) -> dict:
    """Support the evolved node + interfaces structure.
    If the node uses the new form (template + interfaces list), populate
    legacy flat mac fields for current consumers.
    This allows the YAML to use the clean declarative form while keeping
    everything working during the transition.
    """
    result = dict(node)  # copy

    # If using new interfaces list, derive the flat macs if not already present
    if "interfaces" in result and not result.get("mgmt_mac"):
        for iface in result.get("interfaces", []):
            role = iface.get("role")
            mac = iface.get("mac")
            if role == "mgmt" and mac:
                result["mgmt_mac"] = mac
            elif role == "storage" and mac:
                result["storage_mac"] = mac
            elif role == "migration" and mac:
                result["migration_mac"] = mac
            elif role == "service":
                # simplistic: first service -> service1, second -> service2
                if "service1_mac" not in result:
                    result["service1_mac"] = mac
                else:
                    result["service2_mac"] = mac

    # If template is specified but flavor not, pull from node_templates
    if "template" in result and "flavor" not in result:
        tmpl_name = result["template"]
        tmpl = topology.get("node_templates", {}).get(tmpl_name, {})
        if "flavor" in tmpl:
            result["flavor"] = tmpl["flavor"]
        if "ssh_user" in tmpl and "ssh_user" not in result:
            result["ssh_user"] = tmpl["ssh_user"]

    # Ensure ip and ssh_user are at top level (for profile vms etc.)
    # (they already are in the current YAML)

    return result


def _get_mac(node: dict, role: str) -> str | None:
    """Helper to get mac for a role from either flat legacy fields or the new interfaces list.
    Used by the compiler examples below.
    """
    # legacy flat
    if role == "mgmt":
        return node.get("mgmt_mac")
    if role == "storage":
        return node.get("storage_mac")
    if role == "migration":
        return node.get("migration_mac")
    if role == "service1":
        return node.get("service1_mac")
    if role == "service2":
        return node.get("service2_mac")
    # new interfaces list (preferred in evolved YAML)
    for iface in node.get("interfaces", []):
        if iface.get("role") == role:
            return iface.get("mac")
    return None


def _generate_mac(plan_name: str, node_name: str, role: str, index: int = 0) -> str:
    """Generate a deterministic MAC address unless explicitly provided in the definition.

    Uses a hash of (plan_name, node_name, role, index) to produce stable 02:00:00:0D:62:XX
    addresses in the lab MAC range. For the Harvester/SUSE Virtualization rodeo, this
    allows the definition file to stay high-level while the renderer generates what makes sense.
    """
    key = f"{plan_name}-{node_name}-{role}-{index}"
    h = hashlib.sha256(key.encode()).hexdigest()[:2]
    return f"02:00:00:0D:62:{h.upper()}"


def _generate_uuid(plan_name: str, node_name: str) -> str:
    """Generate deterministic UUID v5 unless provided."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"rodeo-{plan_name}-{node_name}"))


def _generate_hostname(node: dict, template: dict, index: int) -> str:
    """Generate hostname from pattern or sensible default."""
    pattern = template.get("hostname_pattern", "{name}")
    name = node.get("name", f"node{index}")
    # For harvester-style, provide nice labels; can be overridden in definition
    labels = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
    label = labels[index] if index < len(labels) else f"node{index}"
    try:
        return pattern.format(name=name, label=label, index=index)
    except Exception:
        return name


def _render_node(raw_node: dict, node_templates: dict, plan_name: str, index: int) -> dict:
    """Render a single node from the definition (focused on Harvester/SUSE Virtualization).

    Generates MACs, hostnames, UUIDs etc. on the fly *unless* explicitly present
    in the definition file.

    The renderer "makes sense" of the logical model for this rodeo:
    - Uses node_templates for defaults (interfaces blueprint, ssh_user, patterns).
    - For each interface role in the node's interfaces (or template), ensures a mac.
    - Generates hostname/UUID if missing.
    - Explicit values in the definition (as currently used for suse-virt) always win.
    """
    node = dict(raw_node)  # don't mutate original

    template_name = node.get("template") or node.get("flavor", "harvester")
    template = node_templates.get(template_name, {})

    # Basic identity
    if "name" not in node:
        node["name"] = f"node{index}"

    # Hostname: explicit wins, else generate from template pattern or default
    if not node.get("hostname"):
        node["hostname"] = _generate_hostname(node, template, index)

    # UUID: explicit or generate
    if not node.get("uuid"):
        node["uuid"] = _generate_uuid(plan_name, node["name"])

    # Interfaces: this is the key evolution for "cables"
    # If the node doesn't declare interfaces, inherit blueprint from template and generate MACs.
    if "interfaces" not in node:
        blueprint = template.get("interfaces", [])
        generated = []
        for i, spec in enumerate(blueprint):
            role = spec.get("role", f"nic{i}")
            count = spec.get("count", 1)
            for c in range(count):
                iface = dict(spec)  # copy
                iface_role = role if count == 1 else f"{role}{c+1}"
                iface["role"] = iface_role
                if "mac" not in iface or not iface.get("mac"):
                    iface["mac"] = _generate_mac(plan_name, node["name"], iface_role, c)
                iface.pop("count", None)  # don't propagate count to final iface
                # model, pxe etc. come from the template spec
                generated.append(iface)
        node["interfaces"] = generated
    else:
        # Node has explicit interfaces list — generate MACs for any that are missing
        for i, iface in enumerate(node["interfaces"]):
            role = iface.get("role", f"nic{i}")
            if "mac" not in iface or not iface.get("mac"):
                iface["mac"] = _generate_mac(plan_name, node["name"], role, i)

    # Pull flavor and ssh_user from template if not on the node instance
    if "flavor" not in node:
        node["flavor"] = template.get("flavor", template_name)
    if "ssh_user" not in node:
        node["ssh_user"] = template.get("ssh_user", "rancher" if node.get("flavor") == "harvester" else "root")

    return node


# --- Example compiler helpers (the "better way") ---

def _compile_pxe_data(topology: dict, nodes: list) -> dict:
    """Turn the boot + nodes + interface_roles into what pxe_server templates expect.
    Uses the pre-rendered nodes list so that on-the-fly MAC/hostname generation is applied.
    """
    boot = topology.get("boot", {})
    pxe = boot.get("pxe", {})

    # The per-node data that ipxe-node.j2 and config-node.yaml.j2 currently get via the loop over vm_nodes
    pxe_nodes = []
    for node in nodes:
        if node.get("flavor") == "harvester":  # only Harvester nodes PXE boot in this rodeo
            pxe_nodes.append({
                "name": node["name"],
                "mgmt_mac": _get_mac(node, "mgmt"),
                "ip": node["ip"],
                # ... any extra boot params, kernel cmdline additions, etc.
            })

    return {
        "http_port": pxe.get("http_port", 8080),
        "http_bind": pxe.get("http_bind", "192.168.122.1"),
        "http_root": pxe.get("http_root", "/srv/harvester-pxe"),
        "tftp_root": pxe.get("tftp_root", "/var/lib/libvirt/dnsmasq"),
        "ipxe_efi_url": pxe.get("ipxe_efi_url"),
        "nodes": pxe_nodes,
        # The actual iPXE script content and per-node Harvester config can be
        # rendered here from jinja templates inside inventory.py if desired,
        # or still left to the Ansible templates (fed by this data).
    }


def _compile_firewall_rules(topology: dict, nodes: list) -> dict:
    """Produce the exact data structure the firewalld tasks loop over.
    (nodes param available for future target resolution using rendered node data.)
    """
    exposed = topology.get("exposed_services", [])
    network = topology.get("network", {})

    forwards = []
    ports = set()

    for svc in exposed:
        target_addr = None
        if svc.get("target") == "vip":
            # In real code this would come from the rendered network/vip
            target_addr = "192.168.122.10"  # placeholder for the harvester_vip
        elif svc.get("target") == "rancher":
            # find the rancher node ip
            for n in topology.get("nodes", []):
                if n["name"] == "rancher":
                    target_addr = n["ip"]
                    break

        if target_addr:
            forwards.append({
                "port": svc["host_port"],
                "toport": svc["guest_port"],
                "toaddr": target_addr,
                "proto": svc.get("proto", "tcp"),
            })
            ports.add(str(svc["host_port"]))

    return {
        "network_mode": network.get("mode", "nat"),
        "exposed_ports": sorted(list(ports)),
        "port_forwards": forwards,
        # rich rules for intra-cluster, masquerade etc. can be generated here too
        # based on network.cidr and interface_roles.
    }


def _compile_libvirt_network(topology: dict, nodes: list) -> dict:
    """Produce the data that goes into network.xml.j2 and network-pxe.xml.j2.
    Uses the pre-rendered nodes so generation of MACs etc. is respected.
    """
    net = topology.get("network", {})

    # Static DHCP hosts (the "cables" from host to guests)
    dhcp_hosts = []
    for node in nodes:
        mgmt_mac = _get_mac(node, "mgmt")
        if mgmt_mac and "ip" in node:
            dhcp_hosts.append({
                "mac": mgmt_mac,
                "name": node["name"],
                "ip": node["ip"],
            })

    return {
        "name": net.get("name", "default"),
        "bridge": net.get("bridge", "virbr0"),
        "forward_mode": "nat" if net.get("mode") == "nat" else "bridge",
        "cidr": net.get("cidr"),
        "gateway": net.get("gateway"),
        "domain": net.get("domain"),
        "dhcp_hosts": dhcp_hosts,          # this populates the <host mac=... ip=...> entries
        # TFTP/HTTP options for iPXE stage 1/2 are added in the pxe_server extension
    }


def _compile_host_prep(topology: dict) -> dict:
    """Host prep expectations (sysctls, selinux_mode, libvirt ovmf paths, network rules) from the definition.

    These describe the mandatory host configuration for the Harvester nested KVM recipe on SLES 16.
    Passthrough today (role still has the concrete tasks); enables declarative driving later without
    scattering the values in Ansible defaults. See plan Phase 1 and definition host_prep section.
    """
    return topology.get("host_prep", {})


def get_node(cfg: dict, name: str) -> dict:
    """Convenience accessor used by plan_cmd etc."""
    inv = build_inventory(cfg)
    for node in inv["vm_nodes"]:
        if node["name"] == name:
            return node
    raise KeyError(f"Node {name} not found in topology for profile {cfg.get('type')}")


# Example usage (will be called from profile, runner, etc. in the full implementation):
# inv = build_inventory(loaded_plan_cfg)
# for name in inv["start_order"]:
#     ...
# vm = get_node(cfg, "harvester1")
# flavor = vm["flavor"]