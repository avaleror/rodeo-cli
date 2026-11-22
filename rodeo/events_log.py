"""Per-lab deploy events: ``<lab>/.rodeo/events.jsonl``, a built-in event sink.

Every ``DeployRunner`` event, already redacted by the event-sink seam
(:mod:`rodeo.hooks`), becomes one JSON line. Schema v0, one object per line:

    ts      float   Unix time the event was seen
    lab     str     lab directory (realpath)
    phase   str     current phase, or null before the first one
    kind    str     PhaseStarted, PhaseSkipped, PhaseDone, PhaseFailed,
                    LogLine, ProgressUpdate or DeployComplete
    text    str     the log line, failure message, skip reason or progress step
    rc      int     exit code (PhaseFailed only)
    elapsed float   seconds (PhaseDone, ProgressUpdate)
    v       int     schema version (0)

The file is 0600 and written through :mod:`rodeo.labfs` (relative to the lab's
directory fd, never through a symlink or a hard link). At 10 MB it is renamed
to ``events.jsonl.1`` (replacing the previous one) and a new file starts. A
write that fails is logged and dropped: it never fails a deploy.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

EVENTS_FILE = ".rodeo/events.jsonl"
MAX_BYTES = 10 * 1024 * 1024
SCHEMA_VERSION = 0

_warned: set[str] = set()  # one warning per lab, not one per log line


def write_event(record: dict) -> None:
    """Event sink: append ``record`` to its lab's events file."""
    lab = record.get("lab")
    if not lab:
        return
    from .labfs import append_lab_file

    line = json.dumps({**record, "v": SCHEMA_VERSION}, ensure_ascii=False, default=str) + "\n"
    try:
        append_lab_file(lab, EVENTS_FILE, line, 0o600, max_bytes=MAX_BYTES)
    except Exception as exc:  # the deploy goes on without its event log
        if lab not in _warned:
            _warned.add(lab)
            logger.warning("events: cannot write %s/%s: %s", lab, EVENTS_FILE, exc)
