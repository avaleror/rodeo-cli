"""Fleet / workshop inventory loading and host selection."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..config import ConfigError
from ..install_source import DEFAULT_INSTALL_URL, resolve_install_source

_VALID_TARGETS = frozenset({"baremetal", "instruqt"})
# Who may reach the lab UI ports on a rodeo-managed AWS security group.
# operator: this machine's /32 only (default). open: also 0.0.0.0/0, applied
# only by `rodeo fleet open-access` after every lab is up with strong passwords.
_VALID_STUDENT_ACCESS = frozenset({"operator", "open"})


@dataclass(frozen=True)
class FleetHost:
    """One KVM host in a workshop inventory."""

    id: str
    ssh: str  # host or user@host
    public_ip: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    ssh_user: str | None = None  # override defaults.ssh_user when ssh has no user@


_VALID_PORTAL_MODES = frozenset({"open", "roster", "both"})
PORTAL_HOST_ID = "portal"
# Host ids end up in file names (student keys, known_hosts), HTTP headers and the
# portal database; hostnames end up in the Caddyfile. Fail closed on anything else.
_HOST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
# Workshop guide link shown to students: an https URL (e.g. GitHub Pages) or a path
# served by the portal itself (e.g. /guide/). Nothing that could become javascript:.
GUIDE_URL_RE = re.compile(r"^(https://[^\s\"'<>]{4,2000}|/(?!/)[A-Za-z0-9._~/-]{0,200})$")
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)


@dataclass(frozen=True)
class PortalConfig:
    """``portal:`` block (claim portal F5). Never part of ``hosts[]``, so no fleet
    command that fans out over hosts (deploy, status, diagnose, ...) can touch it."""

    enabled: bool = False
    mode: str = "both"  # open (workshop code + email) | roster (invite links) | both
    roster: Path | None = None  # CSV name,email[,host_id]; resolved beside workshop.yaml
    student_ssh: bool = False  # per-lab `student` user + key, :22 opened by open-access
    title: str = ""
    code_letters: int = 4  # random letters in RODEO-XXXX-YYYYMMDD (4-8)
    guide_url: str = ""  # workshop guide link on every student page (https URL or /path)
    hostname: str | None = None  # default portal-<ip-dashed>.sslip.io
    instance_type: str | None = None
    host: str | None = None  # BYO portal machine (user@ip): skips provisioning
    # Written by `fleet provision` (like hosts[]):
    ssh: str | None = None
    public_ip: str | None = None
    provider_id: str | None = None

    @property
    def fqdn(self) -> str | None:
        if self.hostname:
            return self.hostname
        if self.public_ip:
            return f"portal-{self.public_ip.replace('.', '-')}.sslip.io"
        return None

    @property
    def target(self) -> str | None:
        """SSH target for the portal machine (BYO ``host`` wins)."""
        return self.host or self.ssh


def _parse_portal(raw: Any, base: Path, provider: dict[str, Any] | None) -> PortalConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("portal: must be a mapping")
    mode = str(raw.get("mode") or "both").strip().lower()
    if mode not in _VALID_PORTAL_MODES:
        raise ConfigError(
            f"portal.mode must be one of {sorted(_VALID_PORTAL_MODES)}, got: {raw.get('mode')!r}"
        )
    enabled = bool(raw.get("enabled", True))
    roster = None
    if raw.get("roster"):
        roster = (base / str(raw["roster"])).expanduser().resolve()
        if enabled and not roster.is_file():
            raise ConfigError(f"portal.roster file not found: {roster}")
    if enabled and mode == "roster" and roster is None:
        raise ConfigError("portal.mode: roster needs portal.roster (CSV name,email[,host_id])")
    if enabled and provider is not None and str(provider.get("student_access") or "operator") != "open":
        raise ConfigError(
            "portal.enabled needs provider.student_access: open (students could not "
            "reach their labs otherwise)"
        )
    hostname = str(raw["hostname"]).strip() if raw.get("hostname") else None
    if hostname and not _HOSTNAME_RE.match(hostname):
        raise ConfigError(f"portal.hostname is not a valid DNS name: {hostname[:80]!r}")
    try:
        code_letters = int(raw.get("code_letters", 4))
    except (TypeError, ValueError) as exc:
        raise ConfigError("portal.code_letters must be an integer (4-8)") from exc
    if not 4 <= code_letters <= 8:
        raise ConfigError("portal.code_letters must be between 4 and 8")
    guide_url = str(raw.get("guide_url") or "").strip()
    if guide_url and not GUIDE_URL_RE.match(guide_url):
        raise ConfigError(
            "portal.guide_url must be an https:// URL or a path on the portal like /guide/, "
            f"got: {guide_url[:80]!r}"
        )
    labels = raw.get("labels") or {}
    return PortalConfig(
        enabled=enabled,
        mode=mode,
        roster=roster,
        student_ssh=bool(raw.get("student_ssh", False)),
        title=str(raw.get("title") or ""),
        code_letters=code_letters,
        guide_url=guide_url,
        hostname=hostname,
        instance_type=str(raw["instance_type"]).strip() if raw.get("instance_type") else None,
        host=str(raw["host"]).strip() if raw.get("host") else None,
        ssh=str(raw["ssh"]).strip() if raw.get("ssh") else None,
        public_ip=str(raw["public_ip"]).strip() if raw.get("public_ip") else None,
        provider_id=str(labels.get("provider_id")) if isinstance(labels, dict) and labels.get("provider_id") else None,
    )


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
    install_url: str = DEFAULT_INSTALL_URL
    # Git ref of rodeo-cli to run on the hosts. None = leave an existing
    # install alone (only a host without rodeo gets bootstrapped).
    ref: str | None = None
    # True when lab.install_url was set explicitly (fork / air-gapped mirror),
    # so `--ref` knows not to point the installer back at the default URL —
    # install.sh must come from the same ref it checks out.
    install_url_explicit: bool = False
    # None = unknown (git-source labs, or profile not declared here) — fleet
    # access shows every URL it knows how to build. Set explicitly when a
    # profile doesn't expose one of the UIs, e.g. ["harvester"] for
    # harvester-ha (no Rancher node). Deliberately NOT inferred from
    # lab.profile — bundled profile -> component mapping isn't stable enough
    # to hardcode (e.g. the "test" profile's example dir is named
    # harvester-lab-config and has no Rancher component at all).
    lab_components: list[str] | None = None
    # F4 host-acquire (optional)
    provider: dict[str, Any] | None = None

    # Secrets only the operator has (e.g. a lab's operator_secrets: SCC regcode,
    # pre-built image URL): copied by name from the laptop's ~/.rodeo/secrets.yaml
    # to each host before `rodeo up` — over SSH stdin, never in workshop.yaml.
    operator_secrets: list[str] = field(default_factory=list)
    # Extra student UIs for the access sheet, {name: host port} — e.g. a
    # lab-in-a-box lab's exposed_services ({"smlm": 443}).
    ui_ports: dict[str, int] = field(default_factory=dict)
    # F5 claim portal (optional)
    portal: PortalConfig | None = None

    @property
    def ssh_user(self) -> str:
        return str(self.defaults.get("ssh_user") or "root")

    @property
    def student_access(self) -> str:
        """``provider.student_access`` (validated at load), ``operator`` if unset."""
        return str((self.provider or {}).get("student_access") or "operator")

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

    # Resolved (not just read) so lab.ref is validated at load time — before
    # any host is touched — and so install.sh is fetched from the same ref it
    # checks out.
    install_url, lab_ref = resolve_install_source(lab)
    install_url_explicit = bool(str(lab.get("install_url") or "").strip())

    components_raw = lab.get("components")
    lab_components: list[str] | None = None
    if components_raw is not None:
        if not isinstance(components_raw, list) or not all(
            isinstance(c, str) for c in components_raw
        ):
            raise ConfigError("lab.components must be a list of strings")
        lab_components = [c.strip().lower() for c in components_raw]

    secrets_raw = lab.get("operator_secrets") or []
    if not isinstance(secrets_raw, list) or not all(
        isinstance(k, str) and re.fullmatch(r"[A-Za-z0-9_]+", k) for k in secrets_raw
    ):
        raise ConfigError("lab.operator_secrets must be a list of secret names (letters, digits, _)")

    ui_raw = lab.get("ui_ports") or {}
    if not isinstance(ui_raw, dict) or not all(
        isinstance(p, int) and 0 < p < 65536 for p in ui_raw.values()
    ):
        raise ConfigError("lab.ui_ports must map UI names to host port numbers")

    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ConfigError("defaults: must be a mapping")

    provider_raw = raw.get("provider")
    provider: dict[str, Any] | None = None
    if provider_raw is not None:
        if not isinstance(provider_raw, dict):
            raise ConfigError("provider: must be a mapping")
        ptype = str(provider_raw.get("type") or "").strip().lower()
        if not ptype:
            raise ConfigError("provider.type is required when provider: is set")
        provider = dict(provider_raw)
        provider["type"] = ptype
        if "count" in provider and provider["count"] is not None:
            try:
                c = int(provider["count"])
            except (TypeError, ValueError) as exc:
                raise ConfigError("provider.count must be an integer") from exc
            if c < 1 or c > 64:
                raise ConfigError("provider.count must be between 1 and 64")
            provider["count"] = c
        if "student_access" in provider:
            access = str(provider["student_access"] or "").strip().lower()
            if access not in _VALID_STUDENT_ACCESS:
                raise ConfigError(
                    "provider.student_access must be one of "
                    f"{sorted(_VALID_STUDENT_ACCESS)}, got: {provider['student_access']!r}"
                )
            provider["student_access"] = access

    hosts_raw = raw.get("hosts")
    if hosts_raw is None:
        hosts_raw = []
    if not isinstance(hosts_raw, list):
        raise ConfigError("hosts: must be a list")
    if not hosts_raw and provider is None:
        raise ConfigError(
            "hosts: must be a non-empty list (or set provider: for fleet provision)"
        )

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
        if not _HOST_ID_RE.match(hid):
            raise ConfigError(
                f"hosts[{i}].id {hid[:70]!r} must be letters, digits, '.', '_' or '-' "
                "(max 63, starting with a letter or digit)"
            )
        if hid == PORTAL_HOST_ID:
            raise ConfigError(f"host id {PORTAL_HOST_ID!r} is reserved for the claim portal")
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
        ref=lab_ref,
        install_url_explicit=install_url_explicit,
        lab_components=lab_components,
        provider=provider,
        operator_secrets=list(secrets_raw),
        ui_ports={str(k): int(v) for k, v in ui_raw.items()},
        portal=_parse_portal(raw.get("portal"), p.parent, provider),
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


def require_provider(inventory: FleetInventory) -> dict[str, Any]:
    """Return provider config or fail closed."""
    if not inventory.provider:
        raise ConfigError(
            "fleet provision requires provider: in workshop.yaml (e.g. type: aws)"
        )
    return inventory.provider


def desired_host_ids(
    inventory: FleetInventory,
    *,
    host_ids: list[str] | None = None,
) -> list[str]:
    """Resolve which host ids provision should ensure."""
    if host_ids:
        return list(host_ids)
    if inventory.hosts:
        return [h.id for h in inventory.hosts]
    provider = require_provider(inventory)
    count = int(provider.get("count") or 0)
    if count < 1:
        raise ConfigError(
            "provider.count is required when hosts: is empty "
            "(or pass --host / list hosts in workshop.yaml)"
        )
    prefix = str(provider.get("host_id_prefix") or "student-")
    width = max(2, len(str(count)))
    return [f"{prefix}{i:0{width}d}" for i in range(1, count + 1)]


def merge_provisioned_hosts(
    inventory_path: Path,
    provisioned: list[Any],
) -> list[dict[str, Any]]:
    """Merge ProvisionedHost-like objects into workshop.yaml hosts[]; return written entries."""
    from ..providers.base import ProvisionedHost

    p = Path(inventory_path).expanduser().resolve()
    raw = yaml.safe_load(p.read_text()) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Workshop inventory must be a mapping: {p}")

    existing = raw.get("hosts") or []
    if not isinstance(existing, list):
        raise ConfigError("hosts: must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entry in existing:
        if isinstance(entry, dict) and entry.get("id"):
            hid = str(entry["id"])
            by_id[hid] = dict(entry)
            order.append(hid)

    written: list[dict[str, Any]] = []
    for item in provisioned:
        if not isinstance(item, ProvisionedHost):
            raise ConfigError("merge_provisioned_hosts expects ProvisionedHost values")
        labels = dict(item.labels)
        if item.provider_id:
            labels["provider_id"] = item.provider_id
        # drop ephemeral provision_action from persisted YAML
        labels.pop("provision_action", None)
        entry = {
            "id": item.id,
            "ssh": item.ssh,
            "public_ip": item.public_ip,
            "labels": labels,
        }
        if item.id in by_id:
            prev = by_id[item.id]
            # preserve ssh_user if set
            if prev.get("ssh_user") and "ssh_user" not in entry:
                entry["ssh_user"] = prev["ssh_user"]
            by_id[item.id] = entry
        else:
            by_id[item.id] = entry
            order.append(item.id)
        written.append(entry)

    raw["hosts"] = [by_id[hid] for hid in order if hid in by_id]
    p.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))
    return written


def merge_portal(inventory_path: Path, *, ssh: str, public_ip: str, provider_id: str | None) -> None:
    """Write the provisioned portal machine into workshop.yaml ``portal:`` (not hosts[])."""
    p = Path(inventory_path).expanduser().resolve()
    raw = yaml.safe_load(p.read_text()) or {}
    portal = dict(raw.get("portal") or {})
    portal["ssh"] = ssh
    portal["public_ip"] = public_ip
    labels = dict(portal.get("labels") or {})
    if provider_id:
        labels["provider_id"] = provider_id
    portal["labels"] = labels
    raw["portal"] = portal
    p.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))


def clear_portal(inventory_path: Path) -> None:
    """Drop provision-written portal fields after the portal VM is terminated."""
    p = Path(inventory_path).expanduser().resolve()
    raw = yaml.safe_load(p.read_text()) or {}
    portal = raw.get("portal")
    if isinstance(portal, dict):
        for k in ("ssh", "public_ip", "labels"):
            portal.pop(k, None)
        p.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))
