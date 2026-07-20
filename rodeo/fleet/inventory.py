"""Fleet / workshop inventory loading and host selection."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..config import ConfigError

_VALID_TARGETS = frozenset({"baremetal", "instruqt"})


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
    # F2 deploy fields (optional for doctor/status)
    lab_source: str | None = None  # git URL (optional git: prefix)
    lab_branch: str | None = None
    lab_profile: str | None = None  # bundled/custom profile to seed
    lab_target: str = "baremetal"
    deploy_concurrency: int = 4
    harvester_ui_port: int = 8443
    rancher_ui_port: int = 30002
    install_url: str = (
        "https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh"
    )
    # None = unknown (git-source labs, or profile not declared here) — fleet
    # access shows every URL it knows how to build. Set explicitly when a
    # profile doesn't expose one of the UIs, e.g. ["harvester"] for
    # harvester-ha (no Rancher node). Deliberately NOT inferred from
    # lab.profile — bundled profile -> component mapping isn't stable enough
    # to hardcode (e.g. the "test" profile's example dir is named
    # harvester-lab-config and has no Rancher component at all).
    lab_components: list[str] | None = None

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
        raise ConfigError("lab.dir is required (remote path for status/deploy)")

    lab_source = lab.get("source")
    lab_source_s = str(lab_source).strip() if lab_source else None
    lab_branch = lab.get("branch")
    lab_branch_s = str(lab_branch).strip() if lab_branch else None
    lab_profile = lab.get("profile")
    lab_profile_s = str(lab_profile).strip() if lab_profile else None
    lab_target = str(lab.get("target") or "baremetal").strip().lower()
    if lab_target not in _VALID_TARGETS:
        raise ConfigError(
            f"lab.target must be one of {sorted(_VALID_TARGETS)}, got: {lab_target}"
        )

    concurrency = int(lab.get("concurrency") or 4)
    if concurrency < 1 or concurrency > 64:
        raise ConfigError("lab.concurrency must be between 1 and 64")

    ports = lab.get("ports") or {}
    if ports and not isinstance(ports, dict):
        raise ConfigError("lab.ports must be a mapping")
    harvester_port = int(ports.get("harvester") or 8443)
    rancher_port = int(ports.get("rancher") or 30002)

    install_url = str(
        lab.get("install_url")
        or "https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh"
    ).strip()

    components_raw = lab.get("components")
    lab_components: list[str] | None = None
    if components_raw is not None:
        if not isinstance(components_raw, list) or not all(
            isinstance(c, str) for c in components_raw
        ):
            raise ConfigError("lab.components must be a list of strings")
        lab_components = [c.strip().lower() for c in components_raw]

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

    return FleetInventory(
        name=name,
        lab_dir=lab_dir,
        defaults=dict(defaults),
        hosts=hosts,
        lab_source=lab_source_s,
        lab_branch=lab_branch_s,
        lab_profile=lab_profile_s,
        lab_target=lab_target,
        deploy_concurrency=concurrency,
        harvester_ui_port=harvester_port,
        rancher_ui_port=rancher_port,
        install_url=install_url,
        lab_components=lab_components,
    )


def require_deploy_config(inventory: FleetInventory) -> None:
    """Fail closed when neither git source nor profile is set (needed for deploy)."""
    if not inventory.lab_source and not inventory.lab_profile:
        raise ConfigError(
            "fleet deploy requires lab.source (git URL) or lab.profile in workshop.yaml"
        )


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


def host_public_ip(host: FleetHost) -> str | None:
    """Return public_ip or best-effort host part of ssh target."""
    if host.public_ip:
        return host.public_ip
    target = host.ssh
    if "@" in target:
        target = target.split("@", 1)[1]
    # strip optional :port
    if target.count(":") == 1 and not target.startswith("["):
        target = target.rsplit(":", 1)[0]
    return target or None
