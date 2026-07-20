"""Fleet / workshop inventory loading and host selection."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..config import ConfigError


@dataclass(frozen=True)
class FleetHost:
    """One KVM host in a workshop inventory."""

    id: str
    ssh: str  # host or user@host
    public_ip: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    ssh_user: str | None = None  # override defaults.ssh_user when ssh has no user@


@dataclass
class FleetInventory:
    """Parsed workshop.yaml."""

    name: str
    lab_dir: str
    defaults: dict[str, Any]
    hosts: list[FleetHost]

    @property
    def ssh_user(self) -> str:
        return str(self.defaults.get("ssh_user") or "root")

    @property
    def identity_file(self) -> str | None:
        val = self.defaults.get("identity_file")
        return str(val) if val else None

    @property
    def ssh_options(self) -> list[str]:
        raw = self.defaults.get("ssh_options") or []
        if not isinstance(raw, list):
            raise ConfigError("defaults.ssh_options must be a list of strings")
        return [str(x) for x in raw]


def load_inventory(path: str | Path) -> FleetInventory:
    """Load and validate a workshop inventory YAML file."""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise ConfigError(f"Workshop inventory not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid workshop YAML ({p}): {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Workshop inventory must be a mapping: {p}")

    name = str(raw.get("name") or p.stem)
    lab = raw.get("lab") or {}
    if not isinstance(lab, dict):
        raise ConfigError("lab: must be a mapping")
    lab_dir = str(lab.get("dir") or "").strip()
    if not lab_dir:
        raise ConfigError("lab.dir is required (remote path for status)")

    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ConfigError("defaults: must be a mapping")

    hosts_raw = raw.get("hosts")
    if not isinstance(hosts_raw, list) or not hosts_raw:
        raise ConfigError("hosts: must be a non-empty list")

    seen: set[str] = set()
    hosts: list[FleetHost] = []
    for i, entry in enumerate(hosts_raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"hosts[{i}] must be a mapping")
        hid = str(entry.get("id") or "").strip()
        ssh = str(entry.get("ssh") or "").strip()
        if not hid:
            raise ConfigError(f"hosts[{i}].id is required")
        if hid in seen:
            raise ConfigError(f"duplicate host id: {hid}")
        if not ssh:
            raise ConfigError(f"hosts[{i}].ssh is required (host or user@host)")
        seen.add(hid)
        labels_raw = entry.get("labels") or {}
        if not isinstance(labels_raw, dict):
            raise ConfigError(f"hosts[{i}].labels must be a mapping")
        labels = {str(k): str(v) for k, v in labels_raw.items()}
        public_ip = entry.get("public_ip")
        hosts.append(
            FleetHost(
                id=hid,
                ssh=ssh,
                public_ip=str(public_ip) if public_ip else None,
                labels=labels,
                ssh_user=str(entry["ssh_user"]) if entry.get("ssh_user") else None,
            )
        )

    return FleetInventory(name=name, lab_dir=lab_dir, defaults=dict(defaults), hosts=hosts)


def select_hosts(
    inventory: FleetInventory,
    *,
    ids: list[str] | None = None,
    labels: dict[str, str] | None = None,
) -> list[FleetHost]:
    """Filter inventory hosts by id list and/or label match (AND)."""
    selected = list(inventory.hosts)
    if ids:
        id_set = set(ids)
        unknown = id_set - {h.id for h in selected}
        if unknown:
            raise ConfigError(f"unknown host id(s): {', '.join(sorted(unknown))}")
        selected = [h for h in selected if h.id in id_set]
    if labels:
        for key, value in labels.items():
            selected = [h for h in selected if h.labels.get(key) == value]
    if not selected:
        raise ConfigError("no hosts matched the selection")
    return selected


def parse_label_opts(raw: tuple[str, ...] | list[str]) -> dict[str, str]:
    """Parse repeated ``key=value`` CLI labels into a dict."""
    out: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            raise ConfigError(f"label must be key=value, got: {item}")
        key, value = item.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key:
            raise ConfigError(f"label key empty in: {item}")
        out[key] = value
    return out
