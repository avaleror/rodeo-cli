"""Provider registry (lazy imports so core install stays light)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import ConfigError

if TYPE_CHECKING:
    from .base import HostProvider

_PROVIDERS = frozenset({"aws", "gcp", "vultr", "hetzner"})


def list_providers() -> list[str]:
    return sorted(_PROVIDERS)


def get_provider(name: str) -> HostProvider:
    """Return a provider instance; import optional deps only when needed."""
    key = (name or "").strip().lower()
    if key not in _PROVIDERS:
        raise ConfigError(
            f"unsupported provider.type: {name!r} "
            f"(expected one of {sorted(_PROVIDERS)})"
        )
    if key == "aws":
        from .aws import AwsHostProvider

        return AwsHostProvider()
    if key == "gcp":
        raise ConfigError(
            "provider.type gcp is planned (F4b) — not implemented yet; use aws"
        )
    if key == "vultr":
        raise ConfigError(
            "provider.type vultr is planned (F4c) — not implemented yet; use aws"
        )
    if key == "hetzner":
        raise ConfigError(
            "provider.type hetzner is planned (F4d) — not implemented yet; use aws"
        )
    raise ConfigError(f"unsupported provider.type: {name!r}")
