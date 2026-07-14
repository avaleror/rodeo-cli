"""Abstract base for all rodeo profiles.

A profile is thin by design: it declares *what* a lab looks like (phases, VM
inventory, resources, versions) and lets this base class drive *how* the config
is assembled and phases are dispatched. Concrete profiles should only carry the
data that makes them different — see rancher.py for the minimal example.
"""
from __future__ import annotations

import copy
import logging
from abc import ABC
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from ..config import ConfigError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..engine.runner import DeployEvent, DeployRunner

try:
    from .. import inventory as _inv
except ImportError:
    _inv = None  # type: ignore[assignment]


# libvirt's default image pool. Profiles that don't select a dedicated disk in
# their definition fall back to this.
STORAGE_DEFAULT: dict = {
    "device": "",
    "mount_point": "/var/lib/libvirt/images",
    "image_dir": "/var/lib/libvirt/images",
}

# Versions shared by every profile's management cluster (Rancher Prime on K3s).
# Profile-specific versions (Harvester, Elemental, EIB) extend these with {**BASE_VERSIONS, ...}.
BASE_VERSIONS: dict = {
    "rancher":      "2.14.1",
    "k3s":          "v1.35.3+k3s1",
    "cert_manager": "v1.20.1",
}

# Non-ansible phases and how to stream them: phase -> (DeployRunner method, needs_rancher).
# needs_rancher phases are skipped when the topology has no rancher node.
_STREAM_PHASES: dict[str, tuple[str, bool]] = {
    "boot":      ("stream_boot",      False),
    "cluster":   ("stream_cluster",   False),
    "rancher":   ("stream_rancher",   True),
    "elemental": ("stream_elemental", True),
    "apply":     ("stream_apply",     False),
    "finalise":  ("stream_finalise",  False),
}


class RodeoProfile(ABC):
    """Defines the phases, VM inventory, and phase dispatch for one rodeo type."""

    name: str
    phases: list[str]
    vm_names: list[str]
    ansible_phases: frozenset[str]
    # Phases skipped when deployment_target is "instruqt" (they break image save)
    # unless the user passes --finalise.
    guarded_phases: frozenset[str] = frozenset()
    # Phases whose completion is never cached — they re-run on every rodeo up.
    no_cache_phases: frozenset[str] = frozenset()

    # --- Config data (overridden by concrete profiles) ---
    # Fallback VM inventory used when the definition file cannot be loaded.
    static_vms: dict[str, dict] = {}
    # VM sizing, keyed by flavor/role.
    resources: dict[str, dict] = {}
    # Component versions. For suse-virt these are only a fallback; see below.
    versions: dict[str, str] = {}
    # When True, versions come from the definition file (single source of truth)
    # and `versions` above is only used if the definition can't be loaded.
    versions_from_definition: bool = False

    # --- Config assembly ---
    def default_cfg(self, config_dir: str | None = None) -> dict:
        """Type-specific config defaults, derived from the definition file when present."""
        inv = self._load_inventory(config_dir)
        if inv is not None:
            vms = self._vms_from_inventory(inv) or self._static_vms_copy()
            storage = inv.get("storage", STORAGE_DEFAULT)
            versions = (inv.get("versions") or self.versions) if self.versions_from_definition else self.versions
            ui_extensions = inv.get("rancher", {}).get("ui_extensions", [])
        else:
            logger.warning(
                "Profile %s: falling back to static VM inventory — definition was not loaded",
                self.name,
            )
            vms = self._static_vms_copy()
            storage = STORAGE_DEFAULT
            versions = self.versions
            ui_extensions = []

        cfg = {
            "vms": vms,
            "resources": self.resources,
            "versions": versions,
            "storage": storage,
            # Rancher Prime UI extensions declared in the definition (rancher.ui_extensions).
            # The RancherPhase reconciles these to their pinned versions after import.
            "rancher_ui_extensions": ui_extensions,
        }
        cfg.update(self.extra_cfg())
        # Return a deep copy so callers that merge/mutate the config in place
        # never corrupt the shared class-attribute defaults for later calls.
        return copy.deepcopy(cfg)

    def extra_cfg(self) -> dict:
        """Profile-specific config keys merged on top of the common shape. Override as needed."""
        return {}

    def _default_user(self, node: dict) -> str:
        """SSH user for an inventory node when the definition doesn't set one. Override as needed."""
        return "root"

    def _static_vms_copy(self) -> dict:
        return {name: dict(spec) for name, spec in self.static_vms.items()}

    def _load_inventory(self, config_dir: str | None) -> dict | None:
        """Render the topology from the definition file, or None if it can't be loaded."""
        if _inv is None:
            return None
        try:
            inv_cfg: dict = {"type": self.name}
            if config_dir:
                inv_cfg["config_dir"] = config_dir
            return _inv.build_inventory(inv_cfg)
        except (ConfigError, FileNotFoundError, ValueError):
            raise
        except Exception as exc:
            logger.warning(
                "Failed to load inventory for profile %s (%s: %s)",
                self.name,
                type(exc).__name__,
                exc,
            )
            return None

    def _vms_from_inventory(self, inv: dict) -> dict:
        return {
            node["name"]: {
                "ip": node["ip"],
                "user": node.get("ssh_user", self._default_user(node)),
            }
            for node in inv.get("vm_nodes", [])
        }

    # --- Phase dispatch ---
    def run_phase(
        self,
        phase: str,
        runner: "DeployRunner",
        vars_file: Path,
    ) -> Iterator["DeployEvent"]:
        """Yield DeployEvents for one phase. Sets runner._last_rc."""
        if phase in self.ansible_phases:
            yield from runner.stream_ansible(phase, vars_file)
            return

        spec = _STREAM_PHASES.get(phase)
        if spec is None:
            runner._last_rc = 0
            return

        method, needs_rancher = spec
        if needs_rancher and "rancher" not in runner.cfg.get("vms", {}):
            from ..engine.runner import LogLine
            yield LogLine(f"No Rancher node in topology — skipping {phase} phase.")
            runner._last_rc = 0
            return

        yield from getattr(runner, method)()
