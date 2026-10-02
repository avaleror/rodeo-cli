"""Several instances of one lab on the same host (lab-in-a-box platform).

A plan's `instance: N` (0 = the definition as written) derives everything that
would otherwise collide between two copies of a lab on one host:

  libvirt network   default/virbr0            -> rodeo-iN / rbrN
  subnet            192.168.122.0/24          -> third octet + N (192.168.(122+N).0/24)
  node IPs          192.168.122.20            -> 192.168.(122+N).20
  MACs              02:00:00:5A:00:20         -> third byte = N (02:00:NN:5A:00:20)
  DNS domain        rodeo.lab                 -> iN.rodeo.lab  (so VM names/FQDNs differ)
  host ports        exposed_services 443      -> 443 + N * instance_port_stride (1000)

Each instance gets its own NAT network, so instances can't reach one another
(libvirt only lets a NAT network's guests answer connections they didn't open),
and its own libvirt domains (FQDNs differ). Pure functions: applied to the
topology in inventory.build_inventory().
"""
from __future__ import annotations

import copy
import ipaddress

from .config import ConfigError

MAX_INSTANCE = 99
DEFAULT_PORT_STRIDE = 1000


def instance_number(cfg: dict) -> int:
    value = cfg.get("instance", 0) or 0
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_INSTANCE:
        raise ConfigError(f"instance must be an integer 0..{MAX_INSTANCE} (got {value!r})")
    return value


def port_stride(cfg: dict) -> int:
    value = cfg.get("instance_port_stride", DEFAULT_PORT_STRIDE)
    if not isinstance(value, int) or value < 1:
        raise ConfigError("instance_port_stride must be a positive integer")
    return value


def shift_ip(ip: str, n: int) -> str:
    """Move an IPv4 address n subnets up (third octet + n)."""
    octets = [int(o) for o in str(ip).split(".")]
    if len(octets) != 4 or octets[2] + n > 255:
        raise ConfigError(f"instance {n}: can't shift {ip} into a valid /24")
    octets[2] += n
    return ".".join(str(o) for o in octets)


def shift_cidr(cidr: str, n: int) -> str:
    net = ipaddress.ip_network(cidr, strict=False)
    if net.prefixlen < 24:
        raise ConfigError(f"instance {n}: network {cidr} must be a /24 (or smaller) to be shifted")
    return f"{shift_ip(str(net.network_address), n)}/{net.prefixlen}"


def shift_mac(mac: str, n: int) -> str:
    parts = mac.split(":")
    if len(parts) != 6:
        return mac
    parts[2] = f"{n:02X}"
    return ":".join(parts)


def network_name(n: int, base: str) -> str:
    return base if n == 0 else f"rodeo-i{n}"


def bridge_name(n: int, base: str) -> str:
    return base if n == 0 else f"rbr{n}"


def domain_name(n: int, base: str) -> str:
    return base if n == 0 else f"i{n}.{base}"


def apply_to_topology(topology: dict, n: int, stride: int = DEFAULT_PORT_STRIDE) -> dict:
    """The definition's topology, moved to instance n (n == 0 returns it unchanged)."""
    if n == 0:
        return topology
    topo = copy.deepcopy(topology)
    net = topo.setdefault("network", {})
    net["name"] = network_name(n, net.get("name", "default"))
    net["bridge"] = bridge_name(n, net.get("bridge", "virbr0"))
    net["domain"] = domain_name(n, net.get("domain", "rodeo.lab"))
    for key in ("cidr",):
        if net.get(key):
            net[key] = shift_cidr(net[key], n)
    if net.get("gateway"):
        net["gateway"] = shift_ip(net["gateway"], n)
    rng = net.get("dhcp_range") or {}
    for key in ("start", "end"):
        if rng.get(key):
            rng[key] = shift_ip(rng[key], n)
    for node in topo.get("nodes", []):
        if node.get("ip"):
            node["ip"] = shift_ip(node["ip"], n)
        if node.get("mgmt_mac"):
            node["mgmt_mac"] = shift_mac(node["mgmt_mac"], n)
        for iface in node.get("interfaces", []):
            if iface.get("mac"):
                iface["mac"] = shift_mac(iface["mac"], n)
    for svc in topo.get("exposed_services", []):
        if svc.get("host_port"):
            svc["host_port"] = int(svc["host_port"]) + n * stride
    return topo
