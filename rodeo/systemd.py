"""The systemd units rodeo installs, and restarting them after the code changes.

``install.sh`` installs the Rodeo Builder as ``rodeo-builder.service``: a system
unit when it runs as root, a user unit otherwise. The running server keeps the
Python it started with and reads ``index.html`` once, so code that replaces the
package (``rodeo self-update``) has to restart the unit or the browser is served
a new ``app.js`` against the old markup.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

BUILDER_UNIT = "rodeo-builder.service"

_TIMEOUT = 120


def systemctl() -> list[str] | None:
    """The ``systemctl`` command for the scope rodeo installs its units in
    (system as root, ``--user`` otherwise), or None where systemd is unusable."""
    if sys.platform != "linux" or not Path("/run/systemd/system").is_dir() or not shutil.which("systemctl"):
        return None
    return ["systemctl"] if os.geteuid() == 0 else ["systemctl", "--user"]


def unit_is_active(unit: str = BUILDER_UNIT) -> bool:
    """Whether *unit* is running in rodeo's scope (False where systemd is unusable)."""
    base = systemctl()
    if base is None:
        return False
    try:
        r = subprocess.run([*base, "is-active", "--quiet", unit], timeout=_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def restart_unit(unit: str = BUILDER_UNIT) -> tuple[bool, str]:
    """Restart *unit*; ``(whether it restarted, what went wrong)``.

    Only a running unit is restarted, so a host that does not run it is left alone.
    """
    base = systemctl()
    if base is None:
        return False, "no systemd in this scope"
    if not unit_is_active(unit):
        return False, "{} is not running here".format(unit)
    try:
        r = subprocess.run([*base, "restart", unit], capture_output=True, text=True, timeout=_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()[-300:] or "exit {}".format(r.returncode)
    return True, ""
