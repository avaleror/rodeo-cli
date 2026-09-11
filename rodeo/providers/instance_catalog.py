"""AWS instance-size catalog: three tiers per lab profile (single-host v1).

Fleet multi-host will reuse the same catalog later. Explicit
``provider.instance_type`` always wins over ``provider.instance_tier``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..config import ConfigError

InstanceTier = Literal["budget", "recommended", "performance"]

TIERS: tuple[InstanceTier, ...] = ("budget", "recommended", "performance")


@dataclass(frozen=True)
class InstanceOffer:
    """One pickable EC2 size for a profile."""

    instance_type: str
    label: str
    notes: str


# Profile → tier → offer. Sized for nested KVM (reject tiny types elsewhere).
# Custom / unknown profiles fall back to ``harvester``.
AWS_PROFILE_TIERS: dict[str, dict[InstanceTier, InstanceOffer]] = {
    "rancher": {
        "budget": InstanceOffer(
            "m7i.2xlarge", "budget", "8 vCPU / 32 GiB — Rancher-only labs"
        ),
        "recommended": InstanceOffer(
            "m7i.4xlarge", "recommended", "16 vCPU / 64 GiB — comfortable headroom"
        ),
        "performance": InstanceOffer(
            "i7i.2xlarge", "performance", "local NVMe — faster guest disks"
        ),
    },
    "test": {
        "budget": InstanceOffer(
            "m7i.4xlarge", "budget", "16 vCPU / 64 GiB — 2-node Harvester"
        ),
        "recommended": InstanceOffer(
            "i7i.4xlarge", "recommended", "local NVMe — better nested I/O"
        ),
        "performance": InstanceOffer(
            "i7i.8xlarge", "performance", "more NVMe / CPU for install wall-clock"
        ),
    },
    "harvester-2n": {
        "budget": InstanceOffer(
            "m7i.8xlarge", "budget", "32 vCPU / 128 GiB — 2n + Rancher"
        ),
        "recommended": InstanceOffer(
            "m8id.8xlarge", "recommended",
            "32 vCPU / 128 GiB / a single ~1.9 TiB NVMe device — unlike i7i.8xlarge "
            "(same vCPU/RAM but splits its NVMe across two ~3.4 TiB devices, only "
            "one of which rodeo mounts), m8id.8xlarge's local storage is one device "
            "that matches this profile's real ~1060 GB need (500 GB/Harvester node "
            "+ 60 GB Rancher) without wasting capacity",
        ),
        "performance": InstanceOffer(
            "m7i.metal-24xl", "performance", "bare metal — max nested performance"
        ),
    },
    "harvester-ha": {
        "budget": InstanceOffer(
            "m7i.12xlarge", "budget", "48 vCPU / 192 GiB — 3-node HA, no Rancher"
        ),
        "recommended": InstanceOffer(
            "i7i.8xlarge", "recommended", "local NVMe — preferred for Harvester I/O"
        ),
        "performance": InstanceOffer(
            "m7i.metal-24xl", "performance", "bare metal — max nested performance"
        ),
    },
    "harvester": {
        "budget": InstanceOffer(
            "m7i.16xlarge", "budget", "64 vCPU / 256 GiB — 3-node + Rancher (EBS)"
        ),
        "recommended": InstanceOffer(
            "i7i.8xlarge", "recommended", "local NVMe — preferred for Harvester I/O"
        ),
        "performance": InstanceOffer(
            "m7i.metal-24xl", "performance", "bare metal — max nested performance"
        ),
    },
    # Same topology/guest sizing as "harvester" — a separate catalog entry
    # because the right AWS instance for it is not i7i.8xlarge: same vCPU/RAM,
    # but i7i.8xlarge splits its NVMe across two ~3.4 TiB devices (rodeo only
    # mounts one). m8id.8xlarge (same vCPU/RAM as harvester-2n's pick) gives a
    # single ~1.9 TiB device instead — tight for 3 nodes at a naive 600 GB/node
    # floor (1860 GB, ~40 GB free), which is why AWS_HARVESTER_DISK_GB dropped
    # to 500: 3x500 + 60 (Rancher) = 1560 GB, ~300+ GB free. Live-validated
    # 2026-09-11: all 3 nodes Ready (real etcd HA), Rancher up, both UIs
    # externally reachable, actual NVMe usage only ~205 GB of 1.8 TiB (12%).
    "harvester-aws": {
        "budget": InstanceOffer(
            "m7i.16xlarge", "budget", "64 vCPU / 256 GiB — 3-node + Rancher (EBS)"
        ),
        "recommended": InstanceOffer(
            "m8id.8xlarge", "recommended",
            "32 vCPU / 128 GiB / a single ~1.9 TiB NVMe device — matches this "
            "profile's real ~1560 GB need (500 GB/Harvester node x 3 + 60 GB "
            "Rancher), not split across multiple devices",
        ),
        "performance": InstanceOffer(
            "m7i.metal-24xl", "performance", "bare metal — max nested performance"
        ),
    },
    "suse-edge": {
        "budget": InstanceOffer(
            "m7i.16xlarge", "budget", "64 vCPU / 256 GiB — Edge stack (EBS)"
        ),
        "recommended": InstanceOffer(
            "i7i.8xlarge", "recommended", "local NVMe — preferred for Edge / Harvester I/O"
        ),
        "performance": InstanceOffer(
            "m7i.metal-24xl", "performance", "bare metal — max nested performance"
        ),
    },
}

_DEFAULT_PROFILE_KEY = "harvester"


def normalize_tier(raw: str | None) -> InstanceTier:
    """Parse ``budget|recommended|performance`` (case-insensitive)."""
    if raw is None or str(raw).strip() == "":
        return "recommended"
    key = str(raw).strip().lower()
    if key not in TIERS:
        raise ConfigError(
            f"provider.instance_tier must be one of {list(TIERS)}, got {raw!r}"
        )
    return key  # type: ignore[return-value]


def catalog_for_profile(profile: str) -> dict[InstanceTier, InstanceOffer]:
    """Return the three offers for a lab profile (fallback: harvester)."""
    name = (profile or "").strip() or _DEFAULT_PROFILE_KEY
    if name in AWS_PROFILE_TIERS:
        return AWS_PROFILE_TIERS[name]
    return AWS_PROFILE_TIERS[_DEFAULT_PROFILE_KEY]


def offer_for(profile: str, tier: str | InstanceTier) -> InstanceOffer:
    """Resolve one tier to an InstanceOffer for the profile."""
    t = normalize_tier(str(tier))
    return catalog_for_profile(profile)[t]


def resolve_instance_type(
    *,
    profile: str,
    instance_type: str | None = None,
    instance_tier: str | None = None,
) -> tuple[str, InstanceTier | None]:
    """Return ``(instance_type, tier_used)``.

    Explicit ``instance_type`` wins (tier_used is None). Otherwise map tier
    (default ``recommended``) through the profile catalog.
    """
    explicit = (instance_type or "").strip()
    if explicit:
        return explicit, None
    tier = normalize_tier(instance_tier)
    return offer_for(profile, tier).instance_type, tier
