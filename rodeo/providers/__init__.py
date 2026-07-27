"""Cloud host providers for Fleet F4 (provision → workshop.yaml)."""
from __future__ import annotations

from .base import (
    SINGLE_HOST_ID,
    TAG_HOST_ID,
    TAG_MANAGED_BY,
    TAG_MANAGED_BY_VALUE,
    TAG_WORKSHOP,
    DeprovisionResult,
    HostProvider,
    ProvisionedHost,
    ProvisionSpec,
    ownership_tags,
)
from .registry import get_provider, list_providers

__all__ = [
    "SINGLE_HOST_ID",
    "TAG_HOST_ID",
    "TAG_MANAGED_BY",
    "TAG_MANAGED_BY_VALUE",
    "TAG_WORKSHOP",
    "DeprovisionResult",
    "HostProvider",
    "ProvisionedHost",
    "ProvisionSpec",
    "get_provider",
    "list_providers",
    "ownership_tags",
]
