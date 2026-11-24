"""Cloud instance-size catalog (AWS, GCP): three tiers per lab profile (single-host v1).

Fleet multi-host will reuse the same catalog later. Explicit
``provider.instance_type`` always wins over ``provider.instance_tier``.

Option A: catalog keys are **topology** profile names (``harvester``,
``suse-edge``, …). There is no separate ``*-aws`` catalog entry — AWS is
*where* (``--target aws`` / ``deployment_target: aws``), not a second *what*.
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
# Custom / unknown profiles fall back to ``harvester``. A profile may omit a
# tier that has no sensible pick; asking for it is a ConfigError.
AWS_PROFILE_TIERS: dict[str, dict[InstanceTier, InstanceOffer]] = {
    # Rancher + K3s + RKE2 + 3-node RKE2: guests ~26 GiB / 14 vCPU / ~200 GB.
    "rancher": {
        "budget": InstanceOffer(
            "m7i.4xlarge", "budget", "16 vCPU / 64 GiB, EBS only"
        ),
        "recommended": InstanceOffer(
            "m8id.4xlarge", "recommended",
            "16 vCPU / 64 GiB / a single 950 GB NVMe device, six VMs with headroom",
        ),
        "performance": InstanceOffer(
            "m7i.metal-24xl", "performance", "bare metal, max nested performance"
        ),
    },
    # Rancher + single-node K3s and RKE2: guests ~14 GiB / 8 vCPU / ~110 GB.
    "rancher-test": {
        "budget": InstanceOffer(
            "m7i.2xlarge", "budget", "8 vCPU / 32 GiB, Rancher plus two small clusters"
        ),
        # Picked for wall-clock: local NVMe and 16 vCPU, so Rancher, K3s and
        # RKE2 installing at the same time do not contend for CPU.
        "recommended": InstanceOffer(
            "m8id.4xlarge", "recommended", "16 vCPU / 64 GiB / local NVMe, fastest deploy"
        ),
        "performance": InstanceOffer(
            "m8id.8xlarge", "performance", "32 vCPU / 128 GiB / local NVMe, max headroom"
        ),
    },
    # Plain KVM host, a handful of small test VMs (1 vCPU / 1 GiB each).
    # m7i.2xlarge is the smallest m7i the too-small check accepts.
    "kvm-hosts": {
        "budget": InstanceOffer(
            "m7i.2xlarge", "budget", "8 vCPU / 32 GiB, 3 to 4 small test VMs"
        ),
        "recommended": InstanceOffer(
            "m7i.2xlarge", "recommended", "8 vCPU / 32 GiB, 3 to 4 small test VMs"
        ),
        "performance": InstanceOffer(
            "m7i.4xlarge", "performance", "16 vCPU / 64 GiB, room for bigger VMs"
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
    # Live-validated 2026-09-11 on m8id.8xlarge: all 3 nodes Ready, Rancher up,
    # both UIs reachable, ~12% of the single ~1.9 TiB NVMe used. i7i.8xlarge has
    # the same vCPU/RAM but splits NVMe across two devices (rodeo mounts one).
    "harvester": {
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
    # Same host sizing as harvester — custom/scripts/ (image cache, NFS, one
    # pre-created VM) do not change the EC2 footprint. No budget tier: the
    # EBS-only m7i.16xlarge cost more than recommended ($3.43 vs $2.22/hr,
    # eu-north-1, 2026-09-14) with slower Longhorn I/O. The cheaper path is
    # the 2-node virt-workshop-2n profile.
    "virt-workshop": {
        "recommended": InstanceOffer(
            "m8id.8xlarge", "recommended",
            "32 vCPU / 128 GiB / a single ~1.9 TiB NVMe device — same as "
            "harvester; custom/scripts/ adds an image cache + NFS export "
            "+ one pre-created VM on top, no extra host sizing needed",
        ),
        "performance": InstanceOffer(
            "m7i.metal-24xl", "performance", "bare metal — max nested performance"
        ),
    },
    # lab-in-a-box SMLM workshop: ~30 GiB RAM of VMs, ~450 GB of disk.
    "smlm-workshop": {
        "recommended": InstanceOffer(
            "m8id.4xlarge", "recommended",
            "16 vCPU / 64 GiB / a single 950 GB NVMe device — SMLM server "
            "(16 GiB) + 8 small clients with room for the ~200 GB server image",
        ),
        "performance": InstanceOffer(
            "m7i.metal-24xl", "performance", "bare metal — max nested performance"
        ),
    },
    # Budget-tier sibling of virt-workshop: 2-node Harvester (no etcd HA,
    # like harvester-2n). m8id.4xlarge is the actual intent of this profile's
    # "budget" tier.
    "virt-workshop-2n": {
        "budget": InstanceOffer(
            "m8id.4xlarge", "budget",
            "16 vCPU / 64 GiB / a single 950 GB NVMe device — this profile's "
            "actual intent: 2x400 + 60 (Rancher) = 860 GB fits with margin "
            "via resources.harvester.disk_floor_override_gb (see rodeo-plan.yaml)",
        ),
        "recommended": InstanceOffer(
            "m8id.8xlarge", "recommended",
            "32 vCPU / 128 GiB / 1.9 TiB NVMe — same instance as "
            "virt-workshop's recommended tier, more headroom than 2 "
            "nodes need if you want it anyway",
        ),
        "performance": InstanceOffer(
            "m7i.metal-24xl", "performance", "bare metal — max nested performance"
        ),
    },
    # Guest footprint ~44 GiB RAM / 16 vCPU / ~310 GB disk — much smaller than
    # Harvester. Sized for AWS nested KVM; baremetal/Instruqt ignore this catalog.
    "suse-edge": {
        "budget": InstanceOffer(
            "m7i.4xlarge", "budget",
            "16 vCPU / 64 GiB, EBS only — same size as recommended, just no "
            "local NVMe (genuine budget pick for this smaller guest footprint)",
        ),
        "recommended": InstanceOffer(
            "m8id.4xlarge", "recommended",
            "16 vCPU / 64 GiB / a single 950 GB NVMe device — local NVMe "
            "helps EIB's image-building I/O; ~310 GB guest disk need fits "
            "with ~640 GB to spare",
        ),
        "performance": InstanceOffer(
            "m7i.metal-24xl", "performance", "bare metal — max nested performance"
        ),
    },
    # Starting sizes for the telco guest set (8+4+4 vCPU, 16+8+8 GiB). Not
    # measured. Recommended covers labs 01-06. Performance is the metal size
    # already used in this catalog, for nested KubeVirt on site-co.
    "suse-telco": {
        "budget": InstanceOffer(
            "m7i.4xlarge", "budget",
            "16 vCPU / 64 GiB, EBS only — same vCPU/RAM as recommended, no "
            "local NVMe. Starting pick, not measured.",
        ),
        "recommended": InstanceOffer(
            "m8id.4xlarge", "recommended",
            "16 vCPU / 64 GiB / a single 950 GB NVMe device — labs 01-06 "
            "starting size, not measured",
        ),
        "performance": InstanceOffer(
            "m7i.metal-24xl", "performance",
            "bare metal — lab 07 nested KubeVirt starting size, not measured",
        ),
    },
}


# GCP (Compute Engine), same tiers. Nested KVM needs an Intel series (C4, N4, N2...).
# budget = N4 + Hyperdisk Balanced (no local SSD: the pool sits on the boot disk, sized by
# the control plane); recommended = C4 -lssd (local NVMe, 375 GiB disks striped into one
# pool); performance = a bigger -lssd shape. Bare metal (c3-*-metal from 192 vCPU, ~$9/h,
# us-central1 / europe-west4 only) is left out for now. Sized like the AWS picks above;
# researched 2026-11-24, availability checked in europe-west4 / europe-north1 / us-central1.
def _g(machine: str, tier: str, notes: str) -> InstanceOffer:
    return InstanceOffer(machine, tier, notes)


_LSSD8 = "8 vCPU / 30 GB / 1 x 375 GiB local SSD"
_LSSD16 = "16 vCPU / 60 GB / 2 x 375 GiB local SSD (750 GiB pool)"
_LSSD24 = "24 vCPU / 90 GB / 4 x 375 GiB local SSD (1.5 TiB pool)"
_LSSD32 = "32 vCPU / 120 GB / 5 x 375 GiB local SSD (1.8 TiB pool)"
_HM32 = "32 vCPU / 248 GB / 5 x 375 GiB local SSD, max headroom"

GCP_PROFILE_TIERS: dict[str, dict[InstanceTier, InstanceOffer]] = {
    "rancher": {
        "budget": _g("n4-standard-16", "budget", "16 vCPU / 64 GB, Hyperdisk only"),
        "recommended": _g("c4-standard-16-lssd", "recommended", _LSSD16),
        "performance": _g("c4-standard-24-lssd", "performance", _LSSD24),
    },
    "rancher-test": {
        "budget": _g("n4-standard-8", "budget", "8 vCPU / 32 GB, Hyperdisk only"),
        "recommended": _g("c4-standard-16-lssd", "recommended", _LSSD16 + ", fastest deploy"),
        "performance": _g("c4-standard-32-lssd", "performance", _LSSD32),
    },
    "kvm-hosts": {
        "budget": _g("n4-standard-8", "budget", "8 vCPU / 32 GB, 3 to 4 small test VMs"),
        "recommended": _g("c4-standard-8-lssd", "recommended", _LSSD8),
        "performance": _g("n4-standard-16", "performance", "16 vCPU / 64 GB, room for bigger VMs"),
    },
    "test": {
        "budget": _g("n4-standard-16", "budget", "16 vCPU / 64 GB, 2-node Harvester on Hyperdisk"),
        "recommended": _g("c4-standard-24-lssd", "recommended", _LSSD24),
        "performance": _g("c4-standard-32-lssd", "performance", _LSSD32),
    },
    "harvester-2n": {
        "budget": _g("n4-standard-32", "budget", "32 vCPU / 128 GB, Hyperdisk only"),
        "recommended": _g("c4-standard-32-lssd", "recommended", _LSSD32 + ", fits ~1060 GB"),
        "performance": _g("c4-highmem-32-lssd", "performance", _HM32),
    },
    "harvester-ha": {
        "budget": _g("n4-standard-48", "budget", "48 vCPU / 192 GB, 3-node HA on Hyperdisk"),
        "recommended": _g("c4-standard-32-lssd", "recommended", _LSSD32),
        "performance": _g("c4-highmem-32-lssd", "performance", _HM32),
    },
    "harvester": {
        "budget": _g("n4-standard-64", "budget", "64 vCPU / 256 GB, 3-node + Rancher on Hyperdisk"),
        "recommended": _g("c4-standard-32-lssd", "recommended", _LSSD32 + ", fits ~1560 GB"),
        "performance": _g("c4-highmem-32-lssd", "performance", _HM32),
    },
    "virt-workshop": {
        "recommended": _g("c4-standard-32-lssd", "recommended", _LSSD32),
        "performance": _g("c4-highmem-32-lssd", "performance", _HM32),
    },
    "smlm-workshop": {
        "recommended": _g("c4-standard-16-lssd", "recommended", _LSSD16 + ", fits ~450 GB"),
        "performance": _g("c4-standard-24-lssd", "performance", _LSSD24),
    },
    "virt-workshop-2n": {
        "budget": _g("c4-standard-24-lssd", "budget", _LSSD24 + ", fits 2x400 + 60 GB"),
        "recommended": _g("c4-standard-32-lssd", "recommended", _LSSD32),
        "performance": _g("c4-highmem-32-lssd", "performance", _HM32),
    },
    "suse-edge": {
        "budget": _g("n4-standard-16", "budget", "16 vCPU / 64 GB, Hyperdisk only"),
        "recommended": _g("c4-standard-16-lssd", "recommended", _LSSD16 + ", fits ~310 GB"),
        "performance": _g("c4-standard-24-lssd", "performance", _LSSD24),
    },
    "suse-telco": {
        "budget": _g("n4-standard-16", "budget", "16 vCPU / 64 GB, Hyperdisk only. Not measured."),
        "recommended": _g("c4-standard-16-lssd", "recommended", _LSSD16 + ". Not measured."),
        "performance": _g("c4-highmem-32-lssd", "performance", _HM32 + ". Not measured."),
    },
}

_CATALOGS: dict[str, dict[str, dict[InstanceTier, InstanceOffer]]] = {
    "aws": AWS_PROFILE_TIERS,
    "gcp": GCP_PROFILE_TIERS,
}

_DEFAULT_PROFILE_KEY = "harvester"


def _canonical_profile(profile: str) -> str:
    """Normalize a topology profile name for catalog lookup (old names map to new)."""
    from ..labseed import canonical_profile

    return canonical_profile((profile or "").strip(), warn=False) or _DEFAULT_PROFILE_KEY


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


def catalog_for_profile(profile: str, provider: str = "aws") -> dict[InstanceTier, InstanceOffer]:
    """Return the offers for a lab profile (fallback: harvester), in TIERS order."""
    tiers = _CATALOGS.get(str(provider or "aws").lower())
    if tiers is None:
        raise ConfigError(f"no instance catalog for provider {provider!r}")
    name = _canonical_profile(profile)
    return tiers.get(name, tiers[_DEFAULT_PROFILE_KEY])


def offer_for(profile: str, tier: str | InstanceTier, provider: str = "aws") -> InstanceOffer:
    """Resolve one tier to an InstanceOffer for the profile."""
    t = normalize_tier(str(tier))
    catalog = catalog_for_profile(profile, provider)
    if t not in catalog:
        raise ConfigError(
            f"profile {_canonical_profile(profile)!r} has no {t!r} instance tier; "
            f"choose one of {[x for x in TIERS if x in catalog]}"
        )
    return catalog[t]


def resolve_instance_type(
    *,
    profile: str,
    instance_type: str | None = None,
    instance_tier: str | None = None,
    provider: str = "aws",
) -> tuple[str, InstanceTier | None]:
    """Return ``(instance_type, tier_used)``.

    Explicit ``instance_type`` wins (tier_used is None). Otherwise map tier
    (default ``recommended``) through the profile catalog.
    """
    explicit = (instance_type or "").strip()
    if explicit:
        return explicit, None
    tier = normalize_tier(instance_tier)
    return offer_for(profile, tier, provider).instance_type, tier
