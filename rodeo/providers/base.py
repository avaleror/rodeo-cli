"""Shared HostProvider contract for Fleet cloud host-acquire."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

TAG_MANAGED_BY = "ManagedBy"
TAG_MANAGED_BY_VALUE = "rodeo"
TAG_WORKSHOP = "rodeo-workshop"
TAG_HOST_ID = "rodeo-host-id"
SINGLE_HOST_ID = "primary"


def ownership_tags(workshop: str, host_id: str) -> dict[str, str]:
    """Tags/labels applied to every provisioned instance."""
    return {
        TAG_MANAGED_BY: TAG_MANAGED_BY_VALUE,
        TAG_WORKSHOP: workshop,
        TAG_HOST_ID: host_id,
    }


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
