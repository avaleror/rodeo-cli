"""Fleet provision / deprovision orchestration (F4)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import ConfigError
from ..providers import DeprovisionResult, ProvisionSpec, ProvisionedHost, get_provider
from .inventory import (
    FleetInventory,
    desired_host_ids,
    merge_provisioned_hosts,
    require_provider,
)


def fleet_provision(
    inventory: FleetInventory,
    inventory_path: Path,
    *,
    host_ids: list[str] | None = None,
    wait_ssh: bool = True,
    write_inventory: bool = True,
) -> list[ProvisionedHost]:
    """Ensure cloud hosts exist; optionally merge into workshop.yaml."""
    provider_cfg = require_provider(inventory)
    if not inventory.defaults.get("identity_file") and wait_ssh:
        raise ConfigError(
            "defaults.identity_file is required for provision SSH wait "
            "(or pass --no-wait-ssh)"
        )
    ids = desired_host_ids(inventory, host_ids=host_ids)
    extra = provider_cfg.get("labels") or {}
    if extra and not isinstance(extra, dict):
        raise ConfigError("provider.labels must be a mapping")
    spec = ProvisionSpec(
        workshop=inventory.name,
        host_ids=ids,
        ssh_user=inventory.ssh_user,
        identity_file=inventory.identity_file,
        extra_labels={str(k): str(v) for k, v in dict(extra).items()},
        wait_ssh=wait_ssh,
    )
    provider = get_provider(str(provider_cfg["type"]))
    hosts = provider.provision(spec, provider_cfg)
    if write_inventory:
        merge_provisioned_hosts(inventory_path, hosts)
    return hosts


def fleet_deprovision(
    inventory: FleetInventory,
    *,
    host_ids: list[str] | None = None,
) -> list[DeprovisionResult]:
    """Terminate ownership-tagged cloud instances for this workshop."""
    provider_cfg = require_provider(inventory)
    ids = host_ids if host_ids is not None else desired_host_ids(inventory)
    spec = ProvisionSpec(
        workshop=inventory.name,
        host_ids=ids,
        ssh_user=inventory.ssh_user,
        identity_file=inventory.identity_file,
        wait_ssh=False,
    )
    provider = get_provider(str(provider_cfg["type"]))
    return provider.deprovision(spec, provider_cfg)


def provision_payload(
    workshop: str,
    hosts: list[ProvisionedHost],
    *,
    inventory_path: Path | None = None,
) -> dict[str, Any]:
    return {
        "workshop": workshop,
        "inventory": str(inventory_path) if inventory_path else None,
        "hosts": [
            {
                "id": h.id,
                "ssh": h.ssh,
                "public_ip": h.public_ip,
                "provider_id": h.provider_id,
                "labels": h.labels,
                "action": h.labels.get("provision_action"),
            }
            for h in hosts
        ],
    }


def deprovision_payload(
    workshop: str,
    results: list[DeprovisionResult],
) -> dict[str, Any]:
    return {
        "workshop": workshop,
        "hosts": [
            {
                "id": r.id,
                "ok": r.ok,
                "error": r.error,
                "provider_id": r.provider_id,
                "detail": r.detail,
            }
            for r in results
        ],
    }
