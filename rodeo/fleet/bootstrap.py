"""Remote bootstrap helpers for fleet deploy (ensure rodeo on PATH)."""
from __future__ import annotations

import shlex

from .inventory import FleetInventory


def bootstrap_script(inventory: FleetInventory) -> str:
    """Shell fragment: install rodeo via install.sh if ``rodeo`` is missing."""
    url = shlex.quote(inventory.install_url)
    return (
        "if ! command -v rodeo >/dev/null 2>&1; then "
        f"curl -fsSL {url} | bash; "
        "fi; "
        "command -v rodeo >/dev/null"
    )
