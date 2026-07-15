"""Live host drift vs the desired plan (shared by ``rodeo plan`` and ``--reconcile``).

V1 scope: VM memory/vCPU mismatches (and missing domains for plan display). Topology
add/remove and pxe/cluster reconcile stay manual (``--force`` / ``--from``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .inventory import _fallback_flavor_name, plan_vm_rows, vm_flavor_map


@dataclass(frozen=True)
class VmChange:
    """One VM that differs from the plan (or is missing on the host)."""

    name: str
    kind: str  # "create" | "memory" | "vcpu"
    desired: str
    ip: str = ""
    actual_state: str = ""
    from_value: Any = None
    to_value: Any = None


@dataclass
class DriftReport:
    """Structured desired-vs-actual report.

    ``reachable`` is False when libvirt is unavailable — callers degrade to
    desired-only display and must not treat the host as drifted for reconcile.
    """

    reachable: bool
    vm_changes: list[VmChange] = field(default_factory=list)
    vm_ok: list[tuple[str, str, str, str]] = field(default_factory=list)
    # (name, desired_summary, ip, state_or_desired)
    net_active: bool | None = None

    @property
    def create_count(self) -> int:
        return sum(1 for c in self.vm_changes if c.kind == "create")

    @property
    def change_count(self) -> int:
        return sum(1 for c in self.vm_changes if c.kind in ("memory", "vcpu"))

    @property
    def ok_count(self) -> int:
        return len(self.vm_ok)

    @property
    def vms_plan_drift(self) -> bool:
        """True when plan should flag the vms phase (create or resize drift)."""
        return bool(self.vm_changes)

    @property
    def phases_affected(self) -> frozenset[str]:
        """Phases ``--reconcile`` should reset. V1: memory/vcpu → ``vms`` only."""
        if not self.reachable:
            return frozenset()
        if any(c.kind in ("memory", "vcpu") for c in self.vm_changes):
            return frozenset({"vms"})
        return frozenset()

    def resource_change_lines(self) -> list[str]:
        """Human-readable lines for LogLine / console (memory/vcpu only)."""
        lines: list[str] = []
        for c in self.vm_changes:
            if c.kind == "memory":
                lines.append(f"{c.name}: memory {c.from_value} → {c.to_value} MiB")
            elif c.kind == "vcpu":
                lines.append(f"{c.name}: vcpu {c.from_value} → {c.to_value}")
        return lines


def inspect_host(cfg: dict) -> dict | None:
    """Return ``{vms: {name: VMInfo}, net_active: bool}`` or None if unreachable."""
    try:
        from .engine.libvirt import LibvirtDriver

        vm_names = [name for name, _ in plan_vm_rows(cfg)]
        with LibvirtDriver(cfg["libvirt"]["uri"]) as lv:
            infos = lv.list_vms(vm_names)
            return {
                "vms": {vm.name: vm for vm in infos},
                "net_active": lv.net_is_active("default"),
            }
    except Exception:
        return None


def _flavor_resources(cfg: dict, vm: str, flavors: dict[str, str]) -> dict:
    key = flavors.get(vm, _fallback_flavor_name(vm))
    return cfg.get("resources", {}).get(key, {})


def collect_drift(cfg: dict, actual: dict | None = None) -> DriftReport:
    """Compare plan to host. Pass ``actual`` to reuse an inspection (or test stub)."""
    if actual is None:
        actual = inspect_host(cfg)

    flavors = vm_flavor_map(cfg)
    vm_rows = plan_vm_rows(cfg)
    report = DriftReport(reachable=actual is not None)

    if actual is not None:
        report.net_active = bool(actual.get("net_active"))

    for name, spec in vm_rows:
        res = _flavor_resources(cfg, name, flavors)
        desired = f"{res.get('memory_mib', '?')} MiB / {res.get('vcpu', '?')} vcpu"
        ip = str(spec.get("ip", "") or "")

        if actual is None:
            report.vm_ok.append((name, desired, ip, "desired"))
            continue

        info = actual["vms"].get(name)
        if info is None or info.state == "not found":
            report.vm_changes.append(
                VmChange(name=name, kind="create", desired=desired, ip=ip)
            )
        elif info.memory_mib and res.get("memory_mib") and info.memory_mib != res["memory_mib"]:
            report.vm_changes.append(
                VmChange(
                    name=name,
                    kind="memory",
                    desired=desired,
                    ip=ip,
                    actual_state=info.state,
                    from_value=info.memory_mib,
                    to_value=res["memory_mib"],
                )
            )
        elif info.vcpus and res.get("vcpu") and info.vcpus != res["vcpu"]:
            report.vm_changes.append(
                VmChange(
                    name=name,
                    kind="vcpu",
                    desired=desired,
                    ip=ip,
                    actual_state=info.state,
                    from_value=info.vcpus,
                    to_value=res["vcpu"],
                )
            )
        else:
            report.vm_ok.append((name, desired, ip, info.state))

    return report
