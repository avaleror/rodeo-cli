"""lab-in-a-box platform — rodeo prepares the host, lab-in-a-box builds the lab.

rodeo keeps what it is good at (host prep, firewall DNAT, secrets, the plan as
source of truth) and hands VM creation and every addon to lab-in-a-box's
setup_lab.py, which already knows how to build labs rodeo has no native phase
for (SMLM, Uyuni, NeuVector, ...). The topology still comes from definition.yaml;
per-node lab-in-a-box knobs and addon sections live under lab_in_a_box: in the
plan (see rodeo/labinabox.py for the mapping).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

from .base import RodeoProfile, register_stream_phase

if TYPE_CHECKING:
    from pathlib import Path

    from ..engine.runner import DeployEvent, DeployRunner

register_stream_phase("labinabox_host", "stream_labinabox_host")
register_stream_phase("labinabox", "stream_labinabox")


class LabInABoxProfile(RodeoProfile):
    name = "lab-in-a-box"
    # kvm_host: packages, libvirt, firewall DNAT (rodeo's Ansible role).
    # labinabox_host / labinabox: see rodeo/engine/labinabox_phase.py.
    # custom_scripts: <lab>/custom/scripts/* post-steps, re-run every time.
    phases = ["kvm_host", "labinabox_host", "labinabox", "custom_scripts"]
    vm_names: list[str] = []
    ansible_phases = frozenset(["kvm_host"])
    no_cache_phases = frozenset(["custom_scripts"])

    static_vms: dict[str, dict] = {}
    resources = {"vm": {"memory_mib": 2048, "vcpu": 2, "disk_gb": 30}}
    versions: dict[str, str] = {}

    def phase_key(self, phase: str, cfg: dict) -> str | None:
        """labinabox_host is redone when the lab-in-a-box source changes: the local
        checkout (RODEO_LABINABOX_PATH, with its commit and uncommitted changes) or
        the repo and ref as configured ("latest" stays unresolved, so a new release
        alone does not reinstall)."""
        if phase != "labinabox_host":
            return None
        from .. import labinabox_host as host
        from ..config import ConfigError

        local = host.local_override()
        if local is not None:
            rev = host.local_revision(local)
            return f"local:{local}@{rev}" if rev else f"local:{local}"
        try:
            repo, ref = host.source(cfg)
        except ConfigError:
            return None
        return f"{repo}@{ref}"

    def extra_cfg(self) -> dict:
        # No Harvester nodes: keeps the vars file from demanding Harvester secrets.
        return {"harvester_node_names": []}

    def finalize_cfg(self, cfg: dict) -> dict:
        """An `instance: N` lab: VM addresses, domains and network come from the
        shifted topology (rodeo/instances.py), not the definition as written."""
        from .. import labinabox_host as host
        from ..instances import instance_number

        uri = host.target(cfg)["libvirt_uri"]
        if uri:
            # VMs on an existing lab-in-a-box's hypervisor: day-2 commands go there.
            cfg.setdefault("libvirt", {})["uri"] = uri
        if not instance_number(cfg):
            return self._apply_cloud_addresses(cfg)
        from ..inventory import build_inventory

        inv = build_inventory(cfg)
        net = inv.get("libvirt_network", {})
        cfg["vms"] = self._vms_from_inventory(inv)
        cfg.setdefault("network", {}).update(
            {"dns_domain": net.get("domain"), "gateway": net.get("gateway")})
        return self._apply_cloud_addresses(cfg)

    def _apply_cloud_addresses(self, cfg: dict) -> dict:
        """Cloud VMs' real addresses (known after deploy) replace the definition's."""
        import json
        from pathlib import Path

        from ..engine.labinabox_phase import NODES_RELPATH

        base = cfg.get("config_dir") or cfg.get("plan_dir")
        nodes = Path(base or ".") / NODES_RELPATH
        if base and nodes.is_file():
            for name, ip in json.loads(nodes.read_text()).items():
                if name in cfg.get("vms", {}):
                    cfg["vms"][name]["ip"] = ip
        return cfg

    def _vms_from_inventory(self, inv: dict) -> dict:
        vms = super()._vms_from_inventory(inv)
        # lab-in-a-box names each libvirt domain after the node's FQDN (its lab.json
        # key, see rodeo/labinabox.py); day-2 commands look it up via domain_name().
        domain = inv.get("libvirt_network", {}).get("domain") or "rodeo.lab"
        for node in inv.get("vm_nodes", []):
            vms[node["name"]]["flavor"] = node.get("flavor", "")
            vms[node["name"]]["domain"] = f"{node['name']}.{domain}"
        return vms

    def run_phase(self, phase: str, runner: "DeployRunner", vars_file: "Path") -> Iterator["DeployEvent"]:
        from .. import labinabox_host as host

        if phase == "kvm_host" and (host.effective_mode(runner.cfg) == "existing" or host.cloud(runner.cfg)):
            from ..engine.runner import LogLine

            why = ("the VMs run in a cloud account" if host.cloud(runner.cfg)
                   else "existing lab-in-a-box host, its setup is left as is")
            yield LogLine(f"  kvm_host skipped — {why}")
            runner._last_rc = 0
            return
        yield from super().run_phase(phase, runner, vars_file)

    def success_extra_sections(self, cfg: dict) -> list[str]:
        lines = ["[bold]Lab nodes[/bold]  (built by lab-in-a-box)"]
        for name, vm in cfg.get("vms", {}).items():
            lines.append(f"  {name:<16} {vm.get('ip', '')}")
        facts = (cfg.get("workshop") or {}).get("facts") or {}
        if facts:
            lines.append("")
            lines.append("[bold]Workshop values[/bold]")
            for key, value in facts.items():
                lines.append(f"  {key:<16} {value}")
        return lines

    def success_next_steps(self, cfg: dict) -> list[str]:
        first = next(iter(cfg.get("vms", {})), "")
        return [f"  rodeo ssh {first}{' ' * max(1, 16 - len(first))}# shell into the VM"] if first else []

