"""Third-party extension loading via Python entry points.

A package extends rodeo by declaring an entry point in the ``rodeo.plugins``
group whose target is a zero-argument callable. The callable performs its
registrations through the public APIs:

    # pyproject.toml of the plugin package
    [project.entry-points."rodeo.plugins"]
    my-lab = "my_pkg.rodeo_plugin:register"

    # my_pkg/rodeo_plugin.py
    def register() -> None:
        from rodeo.profiles import register_profile
        from rodeo.profiles.base import register_stream_phase
        from rodeo.providers.registry import register_provider
        register_profile(MyLabProfile())

Discovery is lazy: the registries call :func:`load_plugins` only when a lookup
misses, so plain installs pay no import cost. A plugin that fails to load is
reported as a warning and skipped — it never breaks the CLI.
"""
from __future__ import annotations

import logging
from importlib import metadata

logger = logging.getLogger(__name__)

GROUP = "rodeo.plugins"

_loaded = False


def load_plugins() -> None:
    """Load every ``rodeo.plugins`` entry point once (idempotent)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        entry_points = metadata.entry_points(group=GROUP)
    except Exception as exc:  # metadata backend quirks must never break the CLI
        logger.warning("plugin discovery failed: %s", exc)
        return
    for ep in entry_points:
        try:
            hook = ep.load()
            if callable(hook):
                hook()
        except Exception as exc:
            logger.warning("plugin %r failed to load: %s", ep.name, exc)
