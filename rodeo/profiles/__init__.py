"""Rodeo profile registry."""
from __future__ import annotations

from .base import RodeoProfile
from .rancher import RancherProfile
from .suse_edge import SuseEdgeProfile
from .suse_virt import SuseVirtProfile

_REGISTRY: dict[str, RodeoProfile] = {
    "suse-virt": SuseVirtProfile(),
    "rancher": RancherProfile(),
    "suse-edge": SuseEdgeProfile(),
}


def get_profile(type_name: str) -> RodeoProfile:
    profile = _REGISTRY.get(type_name)
    if profile is None:
        known = ", ".join(_REGISTRY)
        raise ValueError(f"Unknown rodeo type '{type_name}'. Known: {known}")
    return profile


__all__ = ["RodeoProfile", "get_profile"]
