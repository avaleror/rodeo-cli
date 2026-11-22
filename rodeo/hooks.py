"""Extension seams for plugins (rodeo-ai). Each is a no-op until a plugin registers.

rodeo itself runs one built-in sink, the per-lab events file
(:mod:`rodeo.events_log`). A plugin's ``register()`` (see :mod:`rodeo.plugins`)
may call:

- :func:`register_event_sink`: receive one record per ``DeployRunner`` event,
  after redaction. Records are plain dicts (schema in :func:`event_record`).
  A sink that raises is logged and skipped: it never fails a deploy.
- :func:`register_operation_hook`: the implementation behind
  :func:`begin_operation`, called when an entry point that changes a lab or
  hosts starts. It returns an operation id or None; callers ignore None and
  nothing else about them changes either way.
- ``rodeo.mcp.tools.register_mcp_tool``: add an MCP tool next to the built-in table.

Security behaviour never lives in a hook: a plugin that fails to load is
skipped, so rodeo must be safe without it.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import time
from typing import Any, Callable, Iterator

from .plugins import load_plugins

logger = logging.getLogger(__name__)

EventSink = Callable[[dict], None]
OperationHook = Callable[[str | None, str], "str | None"]

_sinks: list[EventSink] = []
_operation_hook: OperationHook | None = None
# Set while a caller holds operation(): nested entry points (fleet up invoking
# fleet provision and fleet deploy) reuse the outer operation, not a new one.
_active: contextvars.ContextVar[tuple[str | None] | None] = contextvars.ContextVar(
    "rodeo_operation", default=None
)


# --- event sinks ------------------------------------------------------------

def register_event_sink(callback: EventSink) -> None:
    """Call ``callback(record)`` after each deploy event, with a redacted copy."""
    if not callable(callback):
        raise TypeError("event sink must be callable")
    if callback not in _sinks:
        _sinks.append(callback)


def unregister_event_sink(callback: EventSink) -> None:
    with contextlib.suppress(ValueError):
        _sinks.remove(callback)


def _builtin_sinks() -> list[EventSink]:
    """Sinks rodeo always runs: the per-lab events file (rodeo/events_log.py)."""
    from .events_log import write_event

    return [write_event]


def event_sinks() -> list[EventSink]:
    """Built-in sinks, then the ones plugins registered."""
    load_plugins()
    return _builtin_sinks() + list(_sinks)


def event_record(event: Any, *, lab: str | None, phase: str | None,
                 redact: Callable[[str], str]) -> dict:
    """Schema v0 record for one runner event: ``ts``, ``lab``, ``phase``,
    ``kind``, ``text``, plus ``rc`` and ``elapsed`` when the event has them.
    ``text`` (and every other string) has gone through ``redact``."""
    kind = type(event).__name__
    phase = getattr(event, "phase", None) or phase
    if kind == "LogLine":
        text = event.line
    elif kind == "PhaseFailed":
        text = event.message
    elif kind == "PhaseSkipped":
        text = event.reason
    elif kind == "ProgressUpdate":
        text = f"{event.step} {event.detail}".strip()
    else:
        text = ""
    record: dict = {
        "ts": time.time(),
        "lab": lab,
        "phase": phase,
        "kind": kind,
        "text": redact(str(text)) if text else "",
    }
    rc = getattr(event, "rc", None)
    if rc is not None:
        record["rc"] = rc
    elapsed = getattr(event, "elapsed", None)
    if elapsed is not None:
        record["elapsed"] = round(float(elapsed), 3)
    if record["phase"] is not None:
        record["phase"] = redact(str(record["phase"]))
    return record


def emit_event(record: dict, sinks: list[EventSink] | None = None) -> None:
    """Hand ``record`` (already redacted) to every sink; each gets its own copy."""
    for sink in _sinks if sinks is None else sinks:
        try:
            sink(dict(record))
        except Exception as exc:  # a sink never fails the deploy
            logger.warning("event sink %r failed: %s", sink, exc)


# --- operations -------------------------------------------------------------

def register_operation_hook(hook: OperationHook | None) -> None:
    """Install (or with None remove) the ``begin_operation`` implementation."""
    global _operation_hook
    if hook is not None and not callable(hook):
        raise TypeError("operation hook must be callable")
    _operation_hook = hook


def begin_operation(lab_dir: str | os.PathLike | None, action: str) -> str | None:
    """Tell a plugin an operation starts. Returns its id, or None (the default).

    ``lab_dir`` is the lab directory (realpath) or, for fleet commands, the
    workshop file. ``action`` names the entry point: ``up``, ``deploy``,
    ``mcp.<tool>``, ``builder.deploy``, ``fleet.up``, ``fleet.deploy``, ...
    A hook that raises is logged and treated as None.
    """
    held = _active.get()
    if held is not None:
        return held[0]
    load_plugins()
    if _operation_hook is None:
        return None
    path = os.path.realpath(lab_dir) if lab_dir is not None else None
    try:
        op_id = _operation_hook(path, action)
    except Exception as exc:
        logger.warning("operation hook failed for %s: %s", action, exc)
        return None
    return op_id if isinstance(op_id, str) and op_id else None


@contextlib.contextmanager
def operation(lab_dir: str | os.PathLike | None, action: str) -> Iterator[str | None]:
    """``begin_operation`` for a block whose nested entry points share it."""
    op_id = begin_operation(lab_dir, action)
    token = _active.set((op_id,))
    try:
        yield op_id
    finally:
        _active.reset(token)
