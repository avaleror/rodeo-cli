"""Instruqt-friendly guest resource presets and vCPU budget checks.

Nested KVM thrash when Σ guest vCPU ≈ host vCPU. For ``deployment_target:
instruqt`` we size Harvester/Rancher so the guest sum stays near ~70% of the
host, and surface an optional preflight warning when a plan exceeds that.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# Keep guest threads under this fraction of host logical CPUs on Instruqt.
INSTRUQT_VCPU_BUDGET_RATIO = 0.70

# Per-flavor caps / floors for nested installs (RKE2 + QEMU still need headroom).
_INSTRUQT_VCPU_CAP: dict[str, int] = {
    "harvester": 8,
    "rancher": 4,
    "eib": 4,
    "edge-node": 2,
}
_INSTRUQT_VCPU_FLOOR: dict[str, int] = {
    "harvester": 4,
    "rancher": 2,
    "eib": 2,
    "edge-node": 1,
}
_INSTRUQT_MEMORY_MIB: dict[str, int] = {
    "harvester": 20480,
    "rancher": 8192,
    "eib": 12288,
    "edge-node": 4096,
}


def flavor_counts_from_definition(path: Path) -> dict[str, int]:
    """Count nodes per resources-flavor key from a lab ``definition.yaml``."""
    if not path.is_file():
        return {}
    import yaml

    raw = yaml.safe_load(path.read_text()) or {}
    defn = raw.get("definition", raw)
    counts: dict[str, int] = {}
    for node in defn.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        flavor = node.get("template") or node.get("flavor")
        if not flavor:
            name = str(node.get("name", ""))
            from .inventory import _fallback_flavor_name

            flavor = _fallback_flavor_name(name)
        counts[str(flavor)] = counts.get(str(flavor), 0) + 1
    return counts


def instruqt_vcpu_budget(host_cpus: int) -> int:
    """Soft guest-vCPU budget for an Instruqt builder (≈70% of host)."""
    if host_cpus <= 0:
        return 0
    return max(1, int(host_cpus * INSTRUQT_VCPU_BUDGET_RATIO))


def compute_instruqt_presets(
    host_cpus: int,
    flavor_counts: dict[str, int],
) -> dict[str, dict[str, int]]:
    """Return per-flavor ``{memory_mib, vcpu}`` suitable for Instruqt nested KVM.

    Distributes the host budget across flavors present in ``flavor_counts``.
    Non-harvester flavors take their cap first; remaining budget is split across
    Harvester nodes and clamped to ``[floor, cap]``. Disk size is never set here.
    """
    counts = {k: v for k, v in flavor_counts.items() if v > 0}
    if not counts:
        return {}

    budget = instruqt_vcpu_budget(host_cpus) or 22  # ≈32-vCPU builder fallback
    presets: dict[str, dict[str, int]] = {}

    reserved = 0
    for flavor, n in counts.items():
        if flavor == "harvester":
            continue
        vcpu = _INSTRUQT_VCPU_CAP.get(flavor, 2)
        presets[flavor] = {
            "memory_mib": _INSTRUQT_MEMORY_MIB.get(flavor, 4096),
            "vcpu": vcpu,
        }
        reserved += vcpu * n

    n_h = counts.get("harvester", 0)
    if n_h:
        remaining = max(0, budget - reserved)
        per = remaining // n_h
        cap = _INSTRUQT_VCPU_CAP["harvester"]
        floor = _INSTRUQT_VCPU_FLOOR["harvester"]
        presets["harvester"] = {
            "memory_mib": _INSTRUQT_MEMORY_MIB["harvester"],
            "vcpu": max(floor, min(cap, per if per > 0 else floor)),
        }

    return presets


def apply_instruqt_resource_presets(
    plan: dict[str, Any],
    *,
    host_cpus: int,
    flavor_counts: dict[str, int],
) -> list[str]:
    """Write Instruqt presets into ``plan["resources"]`` for flavors already declared.

    Preserves ``disk_gb`` and any flavors not in the computed preset map. Returns
    human-readable change notes (empty when nothing changed).
    """
    presets = compute_instruqt_presets(host_cpus, flavor_counts)
    resources = plan.get("resources")
    if not isinstance(resources, dict) or not presets:
        return []

    notes: list[str] = []
    for flavor, values in presets.items():
        block = resources.get(flavor)
        if not isinstance(block, dict):
            continue
        for field, desired in values.items():
            previous = block.get(field)
            if previous == desired:
                continue
            block[field] = desired
            notes.append(f"resources.{flavor}.{field}: {previous} → {desired}")
    return notes


def plan_guest_vcpus(cfg: dict) -> int:
    """Sum planned guest vCPUs across topology VMs (0 if topology unknown)."""
    from .inventory import _fallback_flavor_name, plan_vm_rows, vm_flavor_map

    res = cfg.get("resources", {})
    if not isinstance(res, dict):
        return 0
    flavors = vm_flavor_map(cfg)
    total = 0
    for name, _ in plan_vm_rows(cfg):
        key = flavors.get(name, _fallback_flavor_name(name))
        total += int(res.get(key, {}).get("vcpu") or 0)
    return total


def vcpu_overcommit_detail(cfg: dict, host_cpus: int) -> str | None:
    """Optional warning text when Instruqt guest vCPU sum exceeds the soft budget."""
    if cfg.get("deployment_target", "baremetal") != "instruqt":
        return None
    if host_cpus <= 0:
        return None
    guest = plan_guest_vcpus(cfg)
    if guest <= 0:
        return None
    budget = instruqt_vcpu_budget(host_cpus)
    if guest <= budget:
        return None
    return (
        f"guest vCPUs {guest} > ~{int(INSTRUQT_VCPU_BUDGET_RATIO * 100)}% of "
        f"host ({budget} of {host_cpus}) — nested install will thrash; "
        f"lower resources.*.vcpu or use a larger builder"
    )
