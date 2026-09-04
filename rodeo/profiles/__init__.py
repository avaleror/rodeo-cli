"""Rodeo profile registry.

Built-in profiles are registered below. External code adds its own with
:func:`register_profile` — directly, or through a ``rodeo.plugins`` entry
point (see rodeo/plugins.py), which is discovered lazily on the first lookup
that misses.
"""
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


def register_profile(profile: RodeoProfile, *, replace: bool = False) -> None:
    """Register a profile so ``type: <profile.name>`` in a plan resolves to it."""
    name = getattr(profile, "name", "")
    if not name:
        raise ValueError("profile must define a non-empty .name")
    if name in _REGISTRY and not replace:
        raise ValueError(
            f"profile '{name}' is already registered (pass replace=True to override)"
        )
    _REGISTRY[name] = profile


def list_profile_types() -> list[str]:
    return sorted(_REGISTRY)


def get_profile(type_name: str) -> RodeoProfile:
    profile = _REGISTRY.get(type_name)
    if profile is None:
        # A plugin may provide this type — discover once, then retry.
        from ..plugins import load_plugins

        load_plugins()
        profile = _REGISTRY.get(type_name)
    if profile is None:
        known = ", ".join(_REGISTRY)
        raise ValueError(f"Unknown rodeo type '{type_name}'. Known: {known}")
    return profile


__all__ = ["RodeoProfile", "get_profile", "list_profile_types", "register_profile"]
