"""Shared HostProvider contract for Fleet cloud host-acquire."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..config import ConfigError

TAG_MANAGED_BY = "ManagedBy"
TAG_MANAGED_BY_VALUE = "rodeo"
TAG_WORKSHOP = "rodeo-workshop"
TAG_HOST_ID = "rodeo-host-id"
SINGLE_HOST_ID = "primary"
# Claim portal VM (F5.2): tagged so lab-only logic can tell it apart from labs.
TAG_ROLE = "rodeo-role"
ROLE_PORTAL = "portal"


# Issue #175: who launched a resource, and which deployment it belongs to. Many
# people share one account and reuse lab names, so destroy matches on these
# two, never on the lab name alone.
TAG_OWNER = "rodeo-owner"
TAG_DEPLOYMENT = "rodeo-deployment"
TAG_VERSION = "rodeo-version"
TAG_CREATED_BY = "rodeo-created-by"
OWNER_ENV = "RODEO_OWNER"
MAX_TAG_VALUE = 256  # AWS tag value limit


def ownership_tags(workshop: str, host_id: str) -> dict[str, str]:
    """Tags/labels applied to every provisioned instance."""
    return {
        TAG_MANAGED_BY: TAG_MANAGED_BY_VALUE,
        TAG_WORKSHOP: workshop,
        TAG_HOST_ID: host_id,
    }


def deployment_tags(owner: str, deployment_id: str, version: str) -> dict[str, str]:
    """Identity tags on every billable resource of one deployment."""
    return {
        TAG_OWNER: owner,
        TAG_DEPLOYMENT: deployment_id,
        TAG_VERSION: version,
        TAG_CREATED_BY: owner,
    }


def check_owner(owner: str) -> str:
    """The owner as stored in a tag: never empty, never truncated (a cut ARN
    could merge two callers into one owner)."""
    owner = (owner or "").strip()
    if not owner:
        raise ConfigError(f"empty owner identity (set {OWNER_ENV} or check your cloud login)")
    if len(owner) > MAX_TAG_VALUE:
        raise ConfigError(
            f"owner identity is {len(owner)} characters, over the {MAX_TAG_VALUE}-character "
            f"tag limit; set a shorter {OWNER_ENV}"
        )
    return owner


def reject_reserved_labels(labels: dict[str, Any] | None) -> None:
    """``provider.labels`` may not set rodeo's own keys: ownership and expiry
    tags decide what destroy removes, so a label must never fake one."""
    bad = sorted(
        str(k) for k in (labels or {})
        if str(k) in (TAG_MANAGED_BY, "managed-by") or str(k).startswith("rodeo-")
    )
    if bad:
        raise ConfigError(
            f"provider.labels cannot set {', '.join(bad)}: ManagedBy, managed-by and "
            "rodeo-* keys are reserved for rodeo"
        )


@dataclass(frozen=True)
class ProvisionedHost:
    """One host ready to merge into workshop.yaml ``hosts[]``."""

    id: str
    ssh: str
    public_ip: str
    labels: dict[str, str] = field(default_factory=dict)
    provider_id: str | None = None  # cloud instance id


@dataclass(frozen=True)
class DeprovisionResult:
    id: str
    ok: bool
    error: str | None = None
    provider_id: str | None = None
    detail: str | None = None


@dataclass
class ProvisionSpec:
    """What provision should ensure."""

    workshop: str
    host_ids: list[str]
    ssh_user: str
    identity_file: str | None = None
    extra_labels: dict[str, str] = field(default_factory=dict)
    wait_ssh: bool = True
    # Instance ids this machine recorded for the lab. Destroy falls back to
    # exactly these when the lab's key is not on this machine.
    known_instance_ids: list[str] = field(default_factory=list)
    ssh_timeout: float = 600.0


@runtime_checkable
class HostProvider(Protocol):
    """Cloud adapter: validate → provision/reuse → deprovision by ownership tags."""

    @property
    def name(self) -> str:
        """Stable id: aws | gcp | vultr | hetzner."""

    def validate(self, config: dict[str, Any]) -> None:
        """Fail closed on missing/invalid type-specific fields."""

    def provision(
        self,
        spec: ProvisionSpec,
        config: dict[str, Any],
    ) -> list[ProvisionedHost]:
        """Create or reuse tagged instances; optionally wait for SSH."""

    def deprovision(
        self,
        spec: ProvisionSpec,
        config: dict[str, Any],
    ) -> list[DeprovisionResult]:
        """Destroy only instances with ownership tags for this workshop."""
