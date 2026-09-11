"""Host-context adaptation: shape plan resources/storage for the infrastructure.

Tech platform (``type:``) declares *what* lab. Host context
(``deployment_target:``) declares *where* and how the host must be shaped.
Acquire can be provisioned or BYO; this module applies the same overlays either
way so performance and infra quirks are not tribal knowledge.

Targets are a registry: built-ins (baremetal, instruqt, aws) register below,
and external code adds its own with :func:`register_host_context` — directly,
or through a ``rodeo.plugins`` entry point. An overlay is the customization
point for engine behaviour too: values it sets on the plan (e.g.
``libvirt.disk_cache``/``disk_io``, ``storage.backend``) are honored by
DeployRunner over its per-target defaults.
"""
from __future__ import annotations

import copy
from typing import Any, Callable

# An overlay mutates cfg in place and returns human-readable notes.
HostContextOverlay = Callable[[dict[str, Any], dict[str, Any]], list[str]]

_TARGETS: dict[str, HostContextOverlay] = {}

# AWS / NVMe workshops: flat per-node floors, deliberately well under the
# NVMe device's actual capacity — matches real-world (Instruqt) sizing, not
# "use as much of the local NVMe as possible". The rest of the device is
# intentionally left free.
#
# History: the original 2026-07-30 design floored Harvester at 1200 GB/node
# ("performance-first"), so a 3-node profile demanded ~3.6 TiB — more than a
# single NVMe device provides on most instance types. Caught live 2026-09-11.
# A same-day fix tried a 1200 GB *total* pool budget split per node instead —
# also wrong. Andrés then settled it in steps: flat 300 GB/Harvester-node, then
# 600 once harvester-2n's recommended instance (m8id.8xlarge, 32 vCPU / 128 GiB
# / a single ~1.9 TiB NVMe device) gave headroom to spend — but 600 x 3 nodes
# (harvester-aws) + 60 (Rancher) = 1860 GB leaves only ~40 GB free on that same
# ~1.9 TiB device, too tight. Settled at 500: 500x3 + 60 = 1560 GB, ~300+ GB
# free margin on harvester-aws's m8id.8xlarge, and even more room on
# harvester-2n's 2-node math (500x2 + 60 = 1060 GB).
AWS_HARVESTER_DISK_GB = 500
AWS_RANCHER_DISK_GB = 60

# Only raise disk when unset or below the floor (never shrink an explicit
# larger plan).
_AWS_DISK_FLOORS: dict[str, int] = {
    "harvester": AWS_HARVESTER_DISK_GB,
    "rancher": AWS_RANCHER_DISK_GB,
}


def register_host_context(
    name: str,
    overlay: HostContextOverlay,
    *,
    replace: bool = False,
) -> None:
    """Register a deployment_target overlay: (cfg, host_facts) -> notes.

    Registering makes ``deployment_target: <name>`` pass validation and routes
    plan shaping through ``overlay``. The overlay mutates cfg in place and
    returns note lines shown in the deploy log.
    """
    key = (name or "").strip().lower()
    if not key:
        raise ValueError("deployment_target name must be non-empty")
    if key in _TARGETS and not replace:
        raise ValueError(
            f"deployment_target '{key}' is already registered (pass replace=True to override)"
        )
    _TARGETS[key] = overlay


def known_targets() -> list[str]:
    """All registered deployment_target names (loads plugins first)."""
    from .plugins import load_plugins

    load_plugins()
    return sorted(_TARGETS)


def is_known_target(name: str) -> bool:
    key = (name or "").strip().lower()
    if key in _TARGETS:
        return True
    from .plugins import load_plugins

    load_plugins()
    return key in _TARGETS


def apply_host_context(
    cfg: dict[str, Any],
    host_facts: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Return a shallow-copied cfg with host-context overlays applied.

    ``host_facts`` may include ``cpus``, ``disk_free_gib``, ``image_dir``,
    ``on_ec2``, ``has_nvme``. Notes are human-readable change / warning lines.
    """
    out = copy.deepcopy(cfg)
    notes: list[str] = []
    target = str(out.get("deployment_target") or "baremetal").strip().lower()
    facts = host_facts or {}

    overlay = _TARGETS.get(target)
    if overlay is None:
        from .plugins import load_plugins

        load_plugins()
        overlay = _TARGETS.get(target)
    if overlay is None:
        # Unvalidated/unknown target: shape like baremetal rather than crash.
        overlay = _apply_baremetal
    notes.extend(overlay(out, facts))

    # EC2 + NVMe detection can enable the storage backend even on baremetal BYO.
    if target != "aws" and (facts.get("on_ec2") or facts.get("has_nvme")):
        notes.extend(_ensure_nvme_backend(out, force=bool(facts.get("has_nvme"))))

    return out, notes


def _apply_instruqt(cfg: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    from .sizing import apply_instruqt_resource_presets

    notes: list[str] = []
    cpus = int(facts.get("cpus") or 0)
    if cpus <= 0:
        import os

        cpus = os.cpu_count() or 0
    counts = facts.get("flavor_counts")
    if not isinstance(counts, dict):
        counts = {}
        # Best-effort from profile defaults — seed path already applies presets.
    if counts:
        notes.extend(
            apply_instruqt_resource_presets(cfg, host_cpus=cpus, flavor_counts=counts)
        )
    # Do not bump disk_gb on Instruqt — tracks are sized for smaller disks.
    return notes


def _apply_aws(cfg: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    notes.extend(_ensure_harvester_disk_floor(cfg, _AWS_DISK_FLOORS))
    notes.extend(_ensure_nvme_backend(cfg, force=True))
    # Local NVMe: baremetal-style O_DIRECT once the pool is on instance store.
    libvirt = cfg.setdefault("libvirt", {})
    if not isinstance(libvirt, dict):
        cfg["libvirt"] = {}
        libvirt = cfg["libvirt"]
    if not libvirt.get("disk_cache"):
        libvirt["disk_cache"] = "none"
        notes.append("libvirt.disk_cache: → none (aws/nvme)")
    if not libvirt.get("disk_io"):
        libvirt["disk_io"] = "native"
        notes.append("libvirt.disk_io: → native (aws/nvme)")
    return notes


def _apply_baremetal(cfg: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    free = facts.get("disk_free_gib")
    if free is None:
        return notes
    try:
        free_i = int(free)
    except (TypeError, ValueError):
        return notes
    need = _planned_disk_gb(cfg)
    if need > 0 and free_i >= 0 and free_i < need:
        notes.append(
            f"warn: host has ~{free_i} GiB free but plan needs ~{need} GiB guest disk "
            f"— free space under image_dir or lower resources.*.disk_gb"
        )
    return notes


def _ensure_harvester_disk_floor(
    cfg: dict[str, Any],
    floors: dict[str, int],
) -> list[str]:
    """Raise ``resources.<flavor>.disk_gb`` to a flat per-node floor. Deliberately
    NOT scaled by node count — a 3-node profile gets 3x this value in total,
    same as a 1-node profile gets 1x, leaving the rest of the NVMe device free
    for other things. Never shrinks an explicit larger plan.
    """
    notes: list[str] = []
    resources = cfg.get("resources")
    if not isinstance(resources, dict):
        return notes
    for flavor, floor in floors.items():
        block = resources.get(flavor)
        if not isinstance(block, dict):
            continue
        current = block.get("disk_gb")
        try:
            cur_i = int(current) if current is not None else 0
        except (TypeError, ValueError):
            cur_i = 0
        if cur_i >= floor:
            continue
        block["disk_gb"] = floor
        notes.append(f"resources.{flavor}.disk_gb: {current} → {floor}")
    return notes


def _ensure_nvme_backend(cfg: dict[str, Any], *, force: bool) -> list[str]:
    notes: list[str] = []
    storage = cfg.setdefault("storage", {})
    if not isinstance(storage, dict):
        cfg["storage"] = {}
        storage = cfg["storage"]
    backend = str(storage.get("backend") or "").strip().lower()
    if backend == "nvme":
        return notes
    if force or not backend:
        storage["backend"] = "nvme"
        notes.append("storage.backend: → nvme")
    return notes


def _planned_disk_gb(cfg: dict[str, Any]) -> int:
    from .inventory import _fallback_flavor_name, plan_vm_rows, vm_flavor_map

    res = cfg.get("resources", {})
    if not isinstance(res, dict):
        return 0
    flavors = vm_flavor_map(cfg)
    total = 0
    for name, _ in plan_vm_rows(cfg):
        key = flavors.get(name, _fallback_flavor_name(name))
        try:
            total += int(res.get(key, {}).get("disk_gb") or 0)
        except (TypeError, ValueError):
            continue
    return total


def persist_host_context_notes(cfg: dict[str, Any], notes: list[str]) -> None:
    """Stash adaptation notes on cfg for deploy logs (non-serialized)."""
    cfg["_host_context_notes"] = list(notes)


register_host_context("baremetal", _apply_baremetal)
register_host_context("instruqt", _apply_instruqt)
register_host_context("aws", _apply_aws)
