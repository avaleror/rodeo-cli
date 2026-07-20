"""Fleet access sheet — student UI URLs (no secrets)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .inventory import FleetHost, FleetInventory, host_public_ip


@dataclass(frozen=True)
class HostAccess:
    id: str
    public_ip: str | None
    harvester_url: str | None
    rancher_url: str | None
    note: str | None = None


def access_for_host(inventory: FleetInventory, host: FleetHost) -> HostAccess:
    ip = host_public_ip(host)
    if not ip:
        return HostAccess(
            id=host.id,
            public_ip=None,
            harvester_url=None,
            rancher_url=None,
            note="set public_ip (or use a resolvable ssh host) in workshop.yaml",
        )
    h_port = inventory.harvester_ui_port
    r_port = inventory.rancher_ui_port
    return HostAccess(
        id=host.id,
        public_ip=ip,
        harvester_url=f"https://{ip}:{h_port}",
        rancher_url=f"https://{ip}:{r_port}",
        note="passwords on the host in ~/.rodeo/secrets.yaml — not printed here",
    )


def fleet_access(
    inventory: FleetInventory,
    hosts: list[FleetHost],
) -> list[HostAccess]:
    return [access_for_host(inventory, h) for h in hosts]


def access_payload(workshop: str, rows: list[HostAccess]) -> dict[str, Any]:
    return {
        "workshop": workshop,
        "hosts": [
            {
                "id": r.id,
                "public_ip": r.public_ip,
                "harvester_url": r.harvester_url,
                "rancher_url": r.rancher_url,
                "note": r.note,
            }
            for r in rows
        ],
    }
