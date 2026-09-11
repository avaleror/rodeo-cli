"""Remote bootstrap helpers for fleet deploy (ensure rodeo on PATH)."""
from __future__ import annotations

from ..install_source import bootstrap_fragment
from .inventory import FleetInventory


def bootstrap_script(inventory: FleetInventory) -> str:
    """Shell fragment leaving ``rodeo`` on PATH, per the inventory's install source.

    With ``lab.ref`` (or ``fleet deploy --ref``) the installer runs every time
    and hard-resets the host's checkout to that ref; without one, an existing
    install is left alone. Shared with the AWS path — see ``install_source``.
    """
    return bootstrap_fragment(
        install_url=inventory.install_url,
        ref=inventory.ref,
    )
