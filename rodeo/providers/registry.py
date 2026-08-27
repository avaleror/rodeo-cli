"""Provider registry (lazy imports so core install stays light).

Built-in providers register a factory below. External code adds its own with
:func:`register_provider` — directly, or through a ``rodeo.plugins`` entry
point (see rodeo/plugins.py), which is discovered lazily on the first lookup
that misses. A plugin may also implement one of the planned names (gcp,
vultr, hetzner) — registration takes precedence over the planned stub.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from ..config import ConfigError

if TYPE_CHECKING:
    from .base import HostProvider


def _aws_factory() -> "HostProvider":
    from .aws import AwsHostProvider

    return AwsHostProvider()


_FACTORIES: dict[str, Callable[[], "HostProvider"]] = {
    "aws": _aws_factory,
}

# Named on the roadmap but not implemented; get_provider explains instead of
# reporting them as unknown.
_PLANNED: dict[str, str] = {"gcp": "F4b", "vultr": "F4c", "hetzner": "F4d"}


def register_provider(
    name: str,
    factory: Callable[[], "HostProvider"],
    *,
    replace: bool = False,
) -> None:
    """Register a host-provider factory for ``provider.type: <name>``."""
    key = (name or "").strip().lower()
    if not key:
        raise ValueError("provider name must be non-empty")
    if not callable(factory):
        raise ValueError(f"provider '{key}' factory must be callable")
    if key in _FACTORIES and not replace:
        raise ValueError(
            f"provider '{key}' is already registered (pass replace=True to override)"
        )
    _FACTORIES[key] = factory


def list_providers() -> list[str]:
    return sorted(set(_FACTORIES) | set(_PLANNED))


def get_provider(name: str) -> "HostProvider":
    """Return a provider instance; import optional deps only when needed."""
    key = (name or "").strip().lower()
    factory = _FACTORIES.get(key)
    if factory is None:
        # A plugin may provide this type — discover once, then retry.
        from ..plugins import load_plugins

        load_plugins()
        factory = _FACTORIES.get(key)
    if factory is not None:
        return factory()
    if key in _PLANNED:
        raise ConfigError(
            f"provider.type {key} is planned ({_PLANNED[key]}) — not implemented yet; use aws"
        )
    raise ConfigError(
        f"unsupported provider.type: {name!r} (expected one of {list_providers()})"
    )
