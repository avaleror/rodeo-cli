"""kvm-hosts platform: plain KVM hosts, nothing deployed on them.

rodeo prepares each host (packages, libvirt, a NAT network, firewall) and stops
there. The VMs are someone else's job: a VM manager under test (e.g. a hack
week project), scripts in custom/scripts/, or virsh by hand. With rodeo fleet,
N such hosts make a small KVM cluster; ``provider.host_to_host`` lets them talk
to each other, and this type alone may run without a dead-man switch
(``provider.ttl_hours: off``, see rodeo/providers/deadman.py) so the hosts last
as long as the work does. ``rodeo fleet destroy`` removes them and every VM on them.
"""
from __future__ import annotations

from .base import RodeoProfile


class KvmHostsProfile(RodeoProfile):
    name = "kvm-hosts"
    builder = None
    phases = ["kvm_host", "custom_scripts"]
    native_logins = False
    vm_names: list[str] = []
    ansible_phases = frozenset(["kvm_host"])
    no_cache_phases = frozenset(["custom_scripts"])
    static_vms: dict[str, dict] = {}
    resources: dict[str, dict] = {}
    versions: dict[str, str] = {}

    def extra_cfg(self) -> dict:
        # No Harvester nodes: keeps the vars file from demanding Harvester secrets.
        return {"harvester_node_names": []}

    def success_extra_sections(self, cfg: dict) -> list[str]:
        return [
            "[bold]KVM host ready[/bold]",
            "  libvirt: qemu:///system, NAT network 'default' (192.168.122.0/24)",
        ]

    def success_next_steps(self, cfg: dict) -> list[str]:
        return ["  virsh -c qemu:///system list --all   # VMs on this host"]
