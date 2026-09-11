"""Deploy-engine registry.

An *engine* is the driver that converges a plan: a runner class whose
instances share the DeployRunner protocol — a ``run()`` generator yielding
:mod:`rodeo.engine.events` types, a ``stop`` event, and ``terminate()``.
The plan selects one with the top-level ``engine:`` key (default ``native``);
``rodeo deploy --engine`` / ``rodeo up --engine`` override it.

Built-ins:
  - ``native``      — DeployRunner (Ansible + Python phases on this host)
  - ``lab-in-a-box``— LabInABoxRunner (setup_lab.py on a remote automation VM)

External code adds its own with :func:`register_engine` — directly, or through
a ``rodeo.plugins`` entry point (see rodeo/plugins.py), discovered lazily on
the first lookup that misses.
"""
from __future__ import annotations

from typing import Callable


def _native() -> type:
    from .runner import DeployRunner

    return DeployRunner


def _labinabox() -> type:
    from .labinabox_runner import LabInABoxRunner

    return LabInABoxRunner


# name -> zero-arg factory returning the runner class (lazy so importing the
# registry never drags in every engine's dependencies).
_ENGINES: dict[str, Callable[[], type]] = {
    "native": _native,
    "lab-in-a-box": _labinabox,
}


def register_engine(
    name: str, engine: type | Callable[[], type], *, replace: bool = False
) -> None:
    """Register a runner class (or a zero-arg factory returning one)."""
    if not name:
        raise ValueError("engine must have a non-empty name")
    if name in _ENGINES and not replace:
        raise ValueError(
            f"engine '{name}' is already registered (pass replace=True to override)"
        )
    _ENGINES[name] = (lambda: engine) if isinstance(engine, type) else engine


def list_engines() -> list[str]:
    return sorted(_ENGINES)


def is_known_engine(name: str) -> bool:
    if name not in _ENGINES:
        from ..plugins import load_plugins

        load_plugins()
    return name in _ENGINES


def get_engine(name: str) -> type:
    factory = _ENGINES.get(name)
    if factory is None:
        # A plugin may provide this engine — discover once, then retry.
        from ..plugins import load_plugins

        load_plugins()
        factory = _ENGINES.get(name)
    if factory is None:
        known = ", ".join(sorted(_ENGINES))
        raise ValueError(f"Unknown engine '{name}'. Known: {known}")
    return factory()


__all__ = ["get_engine", "is_known_engine", "list_engines", "register_engine"]
