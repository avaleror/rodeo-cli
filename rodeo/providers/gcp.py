"""GCP Compute Engine host provider (roadmap F4b).

Same contract as the AWS provider: create or reuse instances found by ownership labels,
wait for SSH, and delete only what carries this workshop's labels.

GCP specifics, and how they map from AWS:

- Labels, not tags. Keys and values are lowercase ``[a-z0-9_-]``, at most 63 characters,
  so ``ManagedBy=rodeo`` becomes ``managed-by=rodeo`` and the workshop name is sanitised.
  The exact workshop name also travels in instance metadata (``rodeo-workshop``).
- Network. Projects may have no VPC at all (``compute.skipDefaultNetworkCreation``), so rodeo
  creates its own: network and subnet ``rodeo-<region>`` (10.90.0.0/20), one per region so
  ranges never overlap. ``provider.network`` / ``provider.subnetwork`` bring your own.
- Firewall. One rule per deployment, ``rodeo-<workshop>-<deployment>``, targeting the network
  tag of the same name, scoped to this machine's public IP (like the AWS managed security group).
  Firewall rules take no labels, so the rule's description records owner and deployment.
- Ownership (issue #175). Same rule as AWS: what is yours is decided by ``rodeo-deployment`` and
  ``rodeo-owner``, never by the lab name. The owner is the GCP account (or ``RODEO_OWNER``). Labels
  cannot hold ``@`` or ``.``, so the label is a readable form plus a short hash, and the raw value
  travels in metadata (``rodeo-owner``, ``rodeo-created-by``).
- Dead-man switch. Native: ``scheduling.maxRunDuration`` with ``instanceTerminationAction:
  DELETE`` deletes the VM and its disks when the TTL runs out, with no timer in the guest.
- Nested virtualization. ``advancedMachineFeatures.enableNestedVirtualization`` on Intel
  series only (N2, N4, C3, C4, Z3, C2); AMD and Arm series cannot run KVM guests.
- Local SSD. ``-lssd`` shapes bundle 375 GiB NVMe disks, several of them above 8 vCPU. The
  kvm_host role stripes them into one RAID0 pool (``nvme_storage.yml``).
- SSH. The lab's own key goes in ``ssh-keys`` metadata; the guest agent creates the user with
  passwordless sudo.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any, Callable

from ..config import ConfigError
from ..fleet.inventory import FleetHost, FleetInventory
from ..fleet.ssh_exec import forget_host_key, run_remote
from ..ssh_key import (
    LabKey,
    delete_lab_ssh_key,
    load_lab_ssh_key,
    new_lab_ssh_key,
    plant_rodeo_ssh_key,
)
from . import deadman
from .base import (
    OWNER_ENV,
    TAG_CREATED_BY,
    TAG_DEPLOYMENT,
    TAG_HOST_ID,
    TAG_MANAGED_BY_VALUE,
    TAG_OWNER,
    TAG_VERSION,
    TAG_WORKSHOP,
    DeprovisionResult,
    ProvisionedHost,
    ProvisionSpec,
    check_owner,
    reject_reserved_labels,
)

DEFAULT_REGION = "europe-west4"
DEFAULT_IMAGE_PROJECT = "suse-cloud"
DEFAULT_IMAGE_FAMILY = "sles-16-1-x86-64"
DEFAULT_SSH_USER = "rodeo"
SUBNET_RANGE = "10.90.0.0/20"
# Same ports as the AWS managed security group: SSH, Harvester UI, Rancher NodePort.
MANAGED_FW_PORTS = (22, 8443, 30002)
# Boot disk when the pool lives on local SSD; without local SSD it must hold the pool.
LSSD_BOOT_DISK_GB = 100
MIN_BOOT_DISK_GB = 100
IDENTITY_SCOPE = "https://www.googleapis.com/auth/userinfo.email"

LABEL_MANAGED_BY = "managed-by"
LABEL_WORKSHOP = TAG_WORKSHOP  # "rodeo-workshop": already a valid label key
LABEL_HOST_ID = TAG_HOST_ID  # "rodeo-host-id"
LABEL_EXPIRES_AT = "rodeo-expires-at"  # unix seconds; ISO has ':' which labels forbid
META_WORKSHOP = "rodeo-workshop"  # exact (unsanitised) workshop name
LABEL_OWNER = TAG_OWNER  # readable owner + short hash (labels forbid '@' and '.')
LABEL_DEPLOYMENT = TAG_DEPLOYMENT
LABEL_VERSION = TAG_VERSION
META_OWNER = TAG_OWNER  # the raw owner
META_CREATED_BY = TAG_CREATED_BY
_FW_OWNER_RE = re.compile(r"owner=(\S+) deployment=([0-9a-f]{8})")

# Series that run nested KVM (Intel). Everything else (AMD, Arm, E2) cannot.
NESTED_SERIES = ("n1", "n2", "n4", "c2", "c3", "c4", "z3", "h3", "c4n", "n4d")
# PD-only series; the rest take Hyperdisk Balanced (C4 and N4 support Hyperdisk only).
PD_SERIES = ("n1", "n2", "c2")
MIN_VCPUS = 8

_CAPACITY_ERRORS = ("ZONE_RESOURCE_POOL_EXHAUSTED", "does not have enough resources",
                    "QUOTA_EXCEEDED", "not available in zone", "unsupported in zone")


def default_region() -> str:
    return (os.environ.get("RODEO_GCP_REGION") or "").strip() or DEFAULT_REGION


def label_value(raw: str) -> str:
    """A GCP label value: lowercase ``[a-z0-9_-]``, at most 63 characters."""
    out = re.sub(r"[^a-z0-9_-]+", "-", str(raw).lower()).strip("-")
    return out[:63].rstrip("-")


def resource_name(raw: str) -> str:
    """A GCE resource name: ``[a-z]([-a-z0-9]*[a-z0-9])?``, at most 63 characters."""
    out = re.sub(r"[^a-z0-9-]+", "-", str(raw).lower()).strip("-")
    if not out or not out[0].isalpha():
        out = f"r-{out}"
    return out[:63].rstrip("-")


def machine_series(machine_type: str) -> str:
    return str(machine_type).strip().lower().split("-", 1)[0]


def machine_vcpus(machine_type: str) -> int | None:
    """vCPUs from a predefined name (``c4-standard-32-lssd`` -> 32); None if not encoded."""
    for part in str(machine_type).lower().split("-")[2:]:
        if part.isdigit():
            return int(part)
    return None


def has_local_ssd(machine_type: str) -> bool:
    return "lssd" in str(machine_type).lower()


def boot_disk_type(machine_type: str) -> str:
    return "pd-balanced" if machine_series(machine_type) in PD_SERIES else "hyperdisk-balanced"


def _with_tail(workshop: str, tail: str) -> str:
    """``rodeo-<workshop><tail>`` within 63 characters, cutting the workshop part, never
    the tail (deployment id and host id are what keep names apart)."""
    tail = re.sub(r"[^a-z0-9-]+", "-", tail.lower())
    head = resource_name(f"rodeo-{workshop}")[: max(63 - len(tail), 8)].rstrip("-")
    return resource_name(head + tail)


def workshop_firewall(workshop: str, deployment: str = "") -> str:
    """Firewall rule and network tag. Without a deployment: the name labs used before
    issue #175 (kept for labs adopted from then)."""
    if not deployment:
        return resource_name(f"rodeo-{workshop}")
    return _with_tail(workshop, f"-{deployment}")


def instance_name(workshop: str, host_id: str, deployment: str = "") -> str:
    if not deployment:
        return resource_name(f"rodeo-{workshop}-{host_id}")
    return _with_tail(workshop, f"-{deployment}-{host_id}")


def owner_label(owner: str) -> str:
    """The owner as a label value: readable, plus a hash of the raw value so two
    accounts that sanitise the same (``a.b@x`` and ``a-b@x``) stay two owners."""
    digest = hashlib.sha256(owner.encode()).hexdigest()[:8]
    readable = label_value(owner)[:54].rstrip("-_")
    return f"{readable}-{digest}" if readable else digest


def _adc_account() -> str:
    """The account behind Application Default Credentials (the GCP ``rodeo-owner``)."""
    try:
        import google.auth
        import google.auth.transport.requests

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/userinfo.email"])
        email = getattr(creds, "service_account_email", None)
        if email and email != "default":
            return str(email)
        creds.refresh(google.auth.transport.requests.Request())
        import json
        import urllib.parse
        import urllib.request

        data = urllib.parse.urlencode({"access_token": creds.token}).encode()
        with urllib.request.urlopen("https://oauth2.googleapis.com/tokeninfo", data=data,
                                    timeout=15) as resp:
            info = json.load(resp)
        if info.get("email"):
            return str(info["email"])
    except Exception:  # noqa: BLE001 — fall back to the gcloud account below
        pass
    account = _gcloud_config_value("account")
    if account:
        return account
    raise ConfigError(
        "could not tell which GCP account you are (gcloud auth application-default login), "
        f"or set {OWNER_ENV}"
    )


def caller_owner(account_fn: Callable[[], str] = _adc_account) -> str:
    env = os.environ.get(OWNER_ENV, "").strip()
    if env:
        return check_owner(env)
    return check_owner(account_fn())


def is_mine(inst: dict[str, Any], *, workshop: str, key: LabKey | None, owner: str) -> bool:
    """Same rule as AWS: deployment id + owner, or (labs from before issue #175) the
    workshop plus this machine's lab public key in the instance's ssh-keys metadata."""
    labels, meta = inst.get("labels") or {}, inst.get("metadata") or {}
    if labels.get(LABEL_MANAGED_BY) != TAG_MANAGED_BY_VALUE or key is None:
        return False
    dep = labels.get(LABEL_DEPLOYMENT)
    if dep:
        return (bool(key.deployment) and dep == key.deployment
                and labels.get(LABEL_OWNER) == owner_label(owner)
                and meta.get(META_OWNER, owner) == owner)
    if labels.get(LABEL_WORKSHOP) != label_value(workshop):
        return False
    if meta.get(META_WORKSHOP, workshop) != workshop:
        return False
    try:
        blob = key.public.read_text().split()[1]
    except (OSError, IndexError):
        return False
    return blob in str(meta.get("ssh-keys") or "")


def _fw_description(owner: str, deployment: str) -> str:
    return f"rodeo-cli lab access (managed by rodeo) owner={owner} deployment={deployment}"


def _fw_owner(description: str) -> tuple[str, str]:
    m = _FW_OWNER_RE.search(description or "")
    return (m.group(1), m.group(2)) if m else ("", "")


def _detect_caller_ip() -> str:
    from .aws import _detect_caller_ip as detect

    return detect()


def _is_not_found(exc: BaseException) -> bool:
    return type(exc).__name__ == "NotFound" or getattr(exc, "code", None) == 404


class GceApi:
    """Thin wrapper over google-cloud-compute; tests replace it with a fake."""

    def __init__(self, project: str) -> None:
        try:
            from google.cloud import compute_v1
        except ImportError as exc:  # pragma: no cover - exercised without the extra
            raise ConfigError(
                "GCP needs google-cloud-compute: pip install 'rodeo-cli[gcp]'"
            ) from exc
        self.c = compute_v1
        self.project = project
        self.instances = compute_v1.InstancesClient()
        self.networks = compute_v1.NetworksClient()
        self.subnets = compute_v1.SubnetworksClient()
        self.firewalls = compute_v1.FirewallsClient()
        self.zones = compute_v1.ZonesClient()
        self.machine_types = compute_v1.MachineTypesClient()
        self.images = compute_v1.ImagesClient()

    # -- lookups -------------------------------------------------------------
    def zones_in_region(self, region: str) -> list[str]:
        req = self.c.ListZonesRequest(project=self.project, filter=f'name = "{region}-*"')
        return sorted(z.name for z in self.zones.list(request=req) if z.status == "UP")

    def offers(self, zone: str, machine_type: str) -> bool:
        try:
            self.machine_types.get(project=self.project, zone=zone, machine_type=machine_type)
            return True
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return False
            raise

    def image_from_family(self, project: str, family: str) -> str:
        return self.images.get_from_family(project=project, family=family).self_link

    def find_instances(self, label_filter: str) -> list[dict[str, Any]]:
        req = self.c.AggregatedListInstancesRequest(project=self.project, filter=label_filter)
        out: list[dict[str, Any]] = []
        for zone_key, scoped in self.instances.aggregated_list(request=req):
            for inst in scoped.instances or []:
                out.append(self._as_dict(inst, zone_key.rsplit("/", 1)[-1]))
        return out

    def get_instance(self, zone: str, name: str) -> dict[str, Any] | None:
        try:
            return self._as_dict(self.instances.get(project=self.project, zone=zone, instance=name), zone)
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return None
            raise

    @staticmethod
    def _as_dict(inst: Any, zone: str) -> dict[str, Any]:
        nat_ip = ""
        for nic in inst.network_interfaces or []:
            for ac in nic.access_configs or []:
                if ac.nat_i_p:
                    nat_ip = ac.nat_i_p
        meta = {i.key: i.value for i in (inst.metadata.items if inst.metadata else [])}
        return {
            "name": inst.name, "zone": zone, "status": inst.status, "id": str(inst.id),
            "labels": dict(inst.labels or {}), "public_ip": nat_ip, "metadata": meta,
            "tags": list(inst.tags.items) if inst.tags else [],
            "created": str(getattr(inst, "creation_timestamp", "") or ""),
        }

    # -- writes ----------------------------------------------------------------
    def insert_instance(self, zone: str, body: dict[str, Any]) -> None:
        inst = self.c.Instance(body)
        self.instances.insert(project=self.project, zone=zone, instance_resource=inst).result(timeout=600)

    def set_labels(self, zone: str, name: str, labels: dict[str, str]) -> None:
        """Merge ``labels`` into the instance's labels (needs the current fingerprint)."""
        inst = self.instances.get(project=self.project, zone=zone, instance=name)
        req = self.c.InstancesSetLabelsRequest(
            label_fingerprint=inst.label_fingerprint, labels={**dict(inst.labels or {}), **labels},
        )
        self.instances.set_labels(project=self.project, zone=zone, instance=name,
                                  instances_set_labels_request_resource=req).result(timeout=300)

    def start_instance(self, zone: str, name: str) -> None:
        self.instances.start(project=self.project, zone=zone, instance=name).result(timeout=600)

    def delete_instance(self, zone: str, name: str) -> None:
        self.instances.delete(project=self.project, zone=zone, instance=name).result(timeout=600)

    def ensure_network(self, network: str, region: str, subnet: str, cidr: str) -> None:
        try:
            self.networks.get(project=self.project, network=network)
        except Exception as exc:  # noqa: BLE001
            if not _is_not_found(exc):
                raise
            net = self.c.Network(name=network, auto_create_subnetworks=False,
                                 description="rodeo-cli labs (created by rodeo)")
            self.networks.insert(project=self.project, network_resource=net).result(timeout=300)
        try:
            self.subnets.get(project=self.project, region=region, subnetwork=subnet)
        except Exception as exc:  # noqa: BLE001
            if not _is_not_found(exc):
                raise
            sub = self.c.Subnetwork(
                name=subnet, ip_cidr_range=cidr, region=region,
                network=f"projects/{self.project}/global/networks/{network}",
            )
            self.subnets.insert(project=self.project, region=region,
                                subnetwork_resource=sub).result(timeout=300)

    def get_firewall(self, name: str) -> dict[str, Any] | None:
        try:
            fw = self.firewalls.get(project=self.project, firewall=name)
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return None
            raise
        return {"name": fw.name, "source_ranges": sorted(fw.source_ranges or []),
                "description": str(fw.description or "")}

    def list_firewalls(self) -> list[dict[str, Any]]:
        req = self.c.ListFirewallsRequest(project=self.project)
        return [{"name": fw.name, "description": str(fw.description or "")}
                for fw in self.firewalls.list(request=req) if fw.name.startswith("rodeo-")]

    def upsert_firewall(self, name: str, network: str, ports: tuple[int, ...],
                        source_ranges: list[str], tag: str, exists: bool,
                        description: str = "rodeo-cli lab access (managed by rodeo)") -> None:
        fw = self.c.Firewall(
            name=name,
            network=f"projects/{self.project}/global/networks/{network}",
            direction="INGRESS",
            source_ranges=source_ranges,
            target_tags=[tag],
            allowed=[self.c.Allowed(I_p_protocol="tcp", ports=[str(p) for p in ports])],
            description=description,
        )
        if exists:
            self.firewalls.patch(project=self.project, firewall=name,
                                 firewall_resource=fw).result(timeout=300)
        else:
            self.firewalls.insert(project=self.project, firewall_resource=fw).result(timeout=300)

    def delete_firewall(self, name: str) -> None:
        self.firewalls.delete(project=self.project, firewall=name).result(timeout=300)


def _gcloud_config_project() -> str | None:
    return _gcloud_config_value("project")


def _gcloud_config_value(key: str) -> str | None:
    """``gcloud config get-value <key>``; gcloud is often installed off PATH."""
    import shutil
    import subprocess
    from pathlib import Path

    gcloud = shutil.which("gcloud")
    if not gcloud:
        user_install = Path.home() / "google-cloud-sdk" / "bin" / "gcloud"
        gcloud = str(user_install) if user_install.is_file() else None
    if not gcloud:
        return None
    try:
        out = subprocess.run([gcloud, "config", "get-value", key], capture_output=True,
                             text=True, timeout=30, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out if out and "(unset)" not in out else None


def default_project() -> str:
    """The project from the environment or Application Default Credentials."""
    for var in ("RODEO_GCP_PROJECT", "GOOGLE_CLOUD_PROJECT", "CLOUDSDK_CORE_PROJECT"):
        if os.environ.get(var, "").strip():
            return os.environ[var].strip()
    project = None
    try:
        import google.auth

        creds, project = google.auth.default()
        # User credentials carry no project, but `set-quota-project` stores one.
        project = project or getattr(creds, "quota_project_id", None)
    except Exception:  # noqa: BLE001
        pass
    project = project or _gcloud_config_project()
    if not project:
        raise ConfigError(
            "no GCP project: set provider.project in rodeo-plan.yaml (or GOOGLE_CLOUD_PROJECT), "
            "and log in with: gcloud auth application-default login"
        )
    return str(project)


def credentials_identity() -> tuple[bool, str]:
    """(ok, detail) for Application Default Credentials."""
    try:
        import google.auth
        import google.auth.transport.requests
    except ImportError:
        return False, "google-cloud-compute is not installed (the [gcp] extra)"
    try:
        creds, project = google.auth.default()
        creds.refresh(google.auth.transport.requests.Request())
    except Exception as exc:  # noqa: BLE001
        return False, f"{exc} (run: gcloud auth application-default login)"
    who = getattr(creds, "service_account_email", None) or "application default credentials"
    project = project or getattr(creds, "quota_project_id", None) or _gcloud_config_project()
    return True, f"{who}, project {project or '(unset)'}"


class GcpHostProvider:
    """Provision/reuse/delete GCE instances labelled for a workshop."""

    name = "gcp"

    def __init__(
        self,
        *,
        api: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        caller_ip_fn: Callable[[], str] = _detect_caller_ip,
        account_fn: Callable[[], str] = _adc_account,
    ) -> None:
        self._api = api
        self._sleep = sleep
        self._caller_ip_fn = caller_ip_fn
        self._account_fn = account_fn
        self._owner_cache: str | None = None

    def owner(self, config: dict[str, Any] | None = None) -> str:
        """This caller's raw ``rodeo-owner`` (cached per provider)."""
        if self._owner_cache is None:
            self._owner_cache = caller_owner(self._account_fn)
        return self._owner_cache

    def _deployment(self, key: LabKey) -> str:
        if not key.deployment:
            raise ConfigError(
                f"lab key {key.key_name!r} has no deployment id; remove "
                f"{key.private.parent} and deploy again"
            )
        return key.deployment

    # -- config --------------------------------------------------------------
    def _client(self, config: dict[str, Any]) -> Any:
        if self._api is not None:
            return self._api
        self._api = GceApi(str(config.get("project") or default_project()))
        return self._api

    def validate(self, config: dict[str, Any]) -> None:
        if str(config.get("type") or "").lower() != "gcp":
            raise ConfigError("GcpHostProvider requires provider.type: gcp")
        deadman.ttl_hours(config)  # fail before anything is created
        itype = str(config.get("instance_type") or "").strip()
        tier = str(config.get("instance_tier") or "").strip()
        if not itype and not tier:
            raise ConfigError(
                "provider.instance_type or provider.instance_tier is required for GCP "
                "(tiers: budget | recommended | performance)"
            )
        if tier:
            from .instance_catalog import normalize_tier

            normalize_tier(tier)
        if itype:
            self.check_machine_type(itype)
        zone, region = str(config.get("zone") or ""), str(config.get("region") or "")
        if zone and region and not zone.startswith(f"{region}-"):
            raise ConfigError(f"provider.zone {zone!r} is not in provider.region {region!r}")
        cidrs = config.get("allowed_cidrs")
        if cidrs is not None and (not isinstance(cidrs, list) or not all(isinstance(c, str) for c in cidrs)):
            raise ConfigError("provider.allowed_cidrs must be a list of CIDR strings")
        if int(config.get("boot_disk_gb") or 0) < 0:
            raise ConfigError("provider.boot_disk_gb must be >= 0")

    @staticmethod
    def check_machine_type(itype: str) -> None:
        series = machine_series(itype)
        if series not in NESTED_SERIES:
            raise ConfigError(
                f"provider.instance_type {itype!r}: the {series.upper()} series cannot run "
                "nested KVM on GCE (AMD, Arm and E2 cannot). Use C4 (-lssd for local SSD), "
                "N4 or N2."
            )
        vcpus = machine_vcpus(itype)
        if vcpus is not None and vcpus < MIN_VCPUS:
            raise ConfigError(
                f"provider.instance_type {itype!r} is too small for a nested lab "
                f"(needs at least {MIN_VCPUS} vCPUs)"
            )

    def apply_instance_selection(self, config: dict[str, Any], *, profile: str) -> dict[str, Any]:
        """Fill ``instance_type`` from tier/catalog; return an updated copy."""
        from .instance_catalog import resolve_instance_type

        out = dict(config)
        itype, tier = resolve_instance_type(
            profile=profile,
            instance_type=str(out.get("instance_type") or "") or None,
            instance_tier=str(out.get("instance_tier") or "") or None,
            provider="gcp",
        )
        self.check_machine_type(itype)
        out["instance_type"] = itype
        if tier is not None:
            out["instance_tier"] = tier
        return out

    @staticmethod
    def network_names(config: dict[str, Any]) -> tuple[str, str]:
        region = str(config.get("region") or default_region())
        net = str(config.get("network") or f"rodeo-{region}")
        sub = str(config.get("subnetwork") or f"rodeo-{region}")
        return net, sub

    # -- zones -----------------------------------------------------------------
    def candidate_zones(self, api: Any, config: dict[str, Any]) -> list[str]:
        """Zones to try, in order: the configured one, else each zone offering the type."""
        itype = str(config["instance_type"])
        zone = str(config.get("zone") or "").strip()
        if zone:
            if not api.offers(zone, itype):
                raise ConfigError(f"{itype} is not offered in {zone}: pick another provider.zone")
            return [zone]
        region = str(config.get("region") or default_region())
        zones = [z for z in api.zones_in_region(region) if api.offers(z, itype)]
        if not zones:
            raise ConfigError(
                f"{itype} is not offered in any zone of {region}: pick another provider.region "
                "or instance type"
            )
        return zones

    # -- provision -------------------------------------------------------------
    def provision(self, spec: ProvisionSpec, config: dict[str, Any]) -> list[ProvisionedHost]:
        profile = str(config.get("lab_profile") or config.get("profile") or "harvester")
        cfg = self.apply_instance_selection(config, profile=profile)
        self.validate(cfg)
        reject_reserved_labels(cfg.get("labels"))
        cfg.setdefault("region", default_region())
        api = self._client(cfg)
        lab_key, mine = self._lab_key(api, spec.workshop)
        deployment = self._deployment(lab_key)
        ssh_user = spec.ssh_user or DEFAULT_SSH_USER
        wait_spec = ProvisionSpec(
            workshop=spec.workshop, host_ids=spec.host_ids, ssh_user=ssh_user,
            identity_file=str(lab_key.private), extra_labels=spec.extra_labels,
            wait_ssh=spec.wait_ssh, ssh_timeout=spec.ssh_timeout,
        )
        existing: dict[str, dict[str, Any] | None] = {h: None for h in spec.host_ids}
        for inst in mine:
            hid = inst["labels"].get(LABEL_HOST_ID)
            for h in spec.host_ids:
                if hid == label_value(h) and existing[h] is None:
                    existing[h] = inst
        to_create = [h for h, inst in existing.items() if inst is None]
        zones = self.candidate_zones(api, cfg) if to_create else []
        net, sub = self.network_names(cfg)
        if not (cfg.get("network") or cfg.get("subnetwork")):
            api.ensure_network(net, str(cfg["region"]), sub, SUBNET_RANGE)
        # A lab adopted from before issue #175 keeps its old firewall rule and tag.
        firewall = self._firewall_of(mine) or workshop_firewall(spec.workshop, deployment)
        self._reconcile_firewall(api, cfg, net, firewall, deployment)
        image = self._image(api, cfg) if to_create else ""

        out: list[ProvisionedHost] = []
        for host_id in spec.host_ids:
            inst = existing[host_id]
            if inst is None:
                inst = self._create(api, wait_spec, cfg, host_id, zones, image, lab_key, firewall)
                action = "create"
            else:
                action = "reuse"
                if inst["status"] in ("TERMINATED", "STOPPED", "SUSPENDED"):
                    api.start_instance(inst["zone"], inst["name"])
            inst = self._wait_running(api, inst["zone"], inst["name"],
                                      timeout=float(cfg.get("wait_timeout") or 600))
            public_ip = inst["public_ip"]
            if action == "create":
                forget_host_key(spec.workshop, public_ip)  # GCE reuses ephemeral IPs
            labels = {"provider": "gcp", "zone": inst["zone"], **spec.extra_labels,
                      **(cfg.get("labels") or {}), "deployment": deployment}
            expiry = inst["labels"].get(LABEL_EXPIRES_AT, "")
            if expiry.isdigit():
                from datetime import datetime, timezone

                labels["expires_at"] = deadman.iso(datetime.fromtimestamp(int(expiry), tz=timezone.utc))
            host = ProvisionedHost(id=host_id, ssh=public_ip, public_ip=public_ip,
                                   labels={str(k): str(v) for k, v in labels.items()},
                                   provider_id=inst["name"])
            if wait_spec.wait_ssh:
                self._wait_ssh(wait_spec, host, timeout=wait_spec.ssh_timeout)
                self._wait_repos(wait_spec, host, timeout=float(cfg.get("repo_timeout") or 600))
                inv = FleetInventory(name=spec.workshop, lab_dir="/", hosts=[],
                                     defaults={"ssh_user": ssh_user, "identity_file": str(lab_key.private)})
                plant_rodeo_ssh_key(inv, FleetHost(id=host.id, ssh=host.ssh, public_ip=host.public_ip),
                                    key=lab_key)
            out.append(ProvisionedHost(
                id=host.id, ssh=host.ssh, public_ip=host.public_ip,
                labels={**host.labels, "provision_action": action}, provider_id=host.provider_id,
            ))
        return out

    def _image(self, api: Any, cfg: dict[str, Any]) -> str:
        image = str(cfg.get("image") or "").strip()
        if image:
            return image
        project = str(cfg.get("image_project") or DEFAULT_IMAGE_PROJECT)
        family = str(cfg.get("image_family") or DEFAULT_IMAGE_FAMILY)
        return api.image_from_family(project, family)

    def instance_body(
        self, cfg: dict[str, Any], *, project: str, workshop: str, host_id: str, zone: str,
        image: str, ssh_user: str, public_key: str, extra_labels: dict[str, str] | None = None,
        lab_host: bool = True, owner: str = "", deployment: str = "", firewall: str = "",
    ) -> dict[str, Any]:
        """The Instance resource (as a dict) for one lab host."""
        from .aws import _rodeo_version

        itype = str(cfg["instance_type"])
        region = zone.rsplit("-", 1)[0]
        _, sub = self.network_names({**cfg, "region": region})
        reject_reserved_labels(cfg.get("labels"))
        labels: dict[str, str] = {}
        for k, v in {**(cfg.get("labels") or {}), **(extra_labels or {})}.items():
            key, val = label_value(k), label_value(v)
            if key and key[0].isalpha():
                labels[key] = val
        # Ownership labels last so nothing above can replace them: deprovision finds
        # instances by these, and a lost one would leak a billing VM.
        ownership = {
            LABEL_MANAGED_BY: TAG_MANAGED_BY_VALUE,
            LABEL_WORKSHOP: label_value(workshop),
        }
        if owner and deployment:
            ownership.update({
                LABEL_OWNER: owner_label(owner),
                LABEL_DEPLOYMENT: deployment,
                LABEL_VERSION: label_value(_rodeo_version()),
            })
        labels.update({**ownership, LABEL_HOST_ID: label_value(host_id)})
        scheduling: dict[str, Any] = {"automatic_restart": True, "on_host_maintenance": "TERMINATE"}
        expiry = deadman.expires_at(cfg)
        if expiry:
            ttl = max(int(expiry.timestamp() - time.time()), 600)
            scheduling.update({"max_run_duration": {"seconds": ttl},
                               "instance_termination_action": "DELETE"})
            labels[LABEL_EXPIRES_AT] = str(int(expiry.timestamp()))
        else:
            labels[LABEL_EXPIRES_AT] = deadman.NEVER
        boot_gb = int(cfg.get("boot_disk_gb") or 0) or (
            LSSD_BOOT_DISK_GB if has_local_ssd(itype) else MIN_BOOT_DISK_GB
        )
        metadata = [
            {"key": "ssh-keys", "value": f"{ssh_user}:{public_key.strip()}"},
            {"key": META_WORKSHOP, "value": workshop},
        ]
        if owner and deployment:
            metadata += [
                {"key": META_OWNER, "value": owner},
                {"key": META_CREATED_BY, "value": owner},
                {"key": TAG_DEPLOYMENT, "value": deployment},
            ]
        firewall = firewall or workshop_firewall(workshop, deployment)
        if lab_host:
            from ..commands.install_deps import zypper_boot_install_script

            # Installs the KVM stack at boot while the laptop still waits for SSH.
            metadata.append({"key": "startup-script", "value": zypper_boot_install_script()})
        nic: dict[str, Any] = {
            "subnetwork": sub if "/" in sub else f"projects/{project}/regions/{region}/subnetworks/{sub}",
        }
        if bool(cfg.get("associate_public_ip", True)):
            nic["access_configs"] = [{"name": "external-nat", "type_": "ONE_TO_ONE_NAT"}]
        body: dict[str, Any] = {
            "name": instance_name(workshop, host_id, deployment),
            "machine_type": f"zones/{zone}/machineTypes/{itype}",
            "labels": labels,
            "tags": {"items": [firewall]},
            "metadata": {"items": metadata},
            "network_interfaces": [nic],
            "disks": [{
                "boot": True,
                "auto_delete": True,
                "initialize_params": {
                    "source_image": image,
                    "disk_size_gb": boot_gb,
                    "disk_type": f"zones/{zone}/diskTypes/{boot_disk_type(itype)}",
                    "labels": dict(ownership),
                },
            }],
            "scheduling": scheduling,
            "advanced_machine_features": {"enable_nested_virtualization": True},
            # SLES PAYG registers its repositories with an identity token from the metadata
            # server, which needs a service account on the VM (gcloud attaches one by default,
            # the API does not: without it registration fails with "Malformed instance data").
            # Only the userinfo.email scope, so the VM's access token reaches no Google API.
            "service_accounts": [{
                "email": str(cfg.get("service_account") or "default"),
                "scopes": list(cfg.get("service_account_scopes") or [IDENTITY_SCOPE]),
            }],
        }
        return body

    def _create(self, api: Any, spec: ProvisionSpec, cfg: dict[str, Any], host_id: str,
                zones: list[str], image: str, key: LabKey, firewall: str = "") -> dict[str, Any]:
        last: BaseException | None = None
        for zone in zones:
            body = self.instance_body(
                cfg, project=str(api.project), workshop=spec.workshop, host_id=host_id,
                zone=zone, image=image, ssh_user=spec.ssh_user,
                public_key=key.public.read_text(), extra_labels=spec.extra_labels,
                owner=self.owner(cfg), deployment=self._deployment(key), firewall=firewall,
            )
            try:
                api.insert_instance(zone, body)
            except Exception as exc:  # noqa: BLE001
                if any(m in str(exc) for m in _CAPACITY_ERRORS) and zone != zones[-1]:
                    last = exc
                    continue  # no capacity here: next zone
                raise ConfigError(f"GCE could not create {body['name']} in {zone}: {exc}") from exc
            inst = api.get_instance(zone, body["name"])
            if inst is None:
                raise ConfigError(f"GCE created {body['name']} in {zone} but cannot read it back")
            return inst
        raise ConfigError(f"no zone had capacity for {cfg['instance_type']}: {last}")

    def _wait_running(self, api: Any, zone: str, name: str, *, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            inst = api.get_instance(zone, name)
            if inst is None:
                raise ConfigError(f"GCE instance {name} in {zone} disappeared")
            if inst["status"] == "RUNNING" and inst["public_ip"]:
                return inst
            self._sleep(5)
        raise ConfigError(f"timed out waiting for GCE instance {name} to be running")

    # SLES PAYG on GCE registers its repositories (guestregister) a minute or two after
    # sshd answers. rodeo's installer runs zypper straight away, and with no repositories
    # it fails ("No repositories defined"). Live-caught 2026-11-24. Images without zypper
    # pass at once.
    _REPOS_READY = ("command -v zypper >/dev/null || exit 0; "
                    "sudo -n zypper -n -q lr >/dev/null 2>&1")

    def _wait_repos(self, spec: ProvisionSpec, host: ProvisionedHost, *, timeout: float) -> None:
        inv = FleetInventory(name=spec.workshop, lab_dir="/", hosts=[],
                             defaults={"ssh_user": spec.ssh_user, "identity_file": spec.identity_file})
        fh = FleetHost(id=host.id, ssh=host.ssh, public_ip=host.public_ip)
        deadline = time.monotonic() + timeout
        while True:
            result = run_remote(inv, fh, ["bash", "-c", self._REPOS_READY], timeout=60.0, as_root=False)
            if result.ok:
                return
            if time.monotonic() >= deadline:
                raise ConfigError(
                    f"{host.public_ip}: the image's package repositories are still not "
                    f"registered after {int(timeout)} s (SLES PAYG registers them at boot; "
                    "check `sudo systemctl status guestregister` on the host)"
                )
            self._sleep(10)

    def _wait_ssh(self, spec: ProvisionSpec, host: ProvisionedHost, *, timeout: float) -> None:
        from .aws import AwsHostProvider

        # Same login + passwordless-sudo probe as AWS: the guest agent creates the user
        # from ssh-keys metadata a little after sshd answers.
        AwsHostProvider(sleep=self._sleep)._wait_ssh(spec, host, timeout=timeout)

    # -- firewall --------------------------------------------------------------
    def _reconcile_firewall(self, api: Any, cfg: dict[str, Any], network: str, name: str,
                            deployment: str) -> None:
        cidrs = cfg.get("allowed_cidrs")
        if not cidrs:
            ip = self._caller_ip_fn()
            cidrs = [f"{ip}/32" if ":" not in ip else f"{ip}/128"]
        current = api.get_firewall(name)
        owner = self.owner(cfg)
        if current is not None:
            fw_owner, fw_dep = _fw_owner(current.get("description", ""))
            if fw_owner and (fw_owner != owner or fw_dep != deployment):
                raise ConfigError(
                    f"firewall rule {name!r} belongs to another deployment or owner "
                    f"({fw_owner}); rodeo will not change it"
                )
        want = sorted(str(c) for c in cidrs)
        description = _fw_description(owner, deployment)
        if (current is not None and current["source_ranges"] == want
                and current.get("description") == description):
            return
        api.upsert_firewall(name, network, MANAGED_FW_PORTS, want, name,
                            exists=current is not None, description=description)

    @staticmethod
    def _firewall_of(lab: list[dict[str, Any]]) -> str:
        """The firewall tag a lab's instances already carry (first one found)."""
        for inst in lab:
            for tag in inst.get("tags") or []:
                if str(tag).startswith("rodeo-"):
                    return str(tag)
        return ""

    def _delete_firewalls(self, api: Any, names: set[str], workshop: str,
                          gone: set[str]) -> list[DeprovisionResult]:
        """Delete these firewall rules unless a live instance (anyone's) still uses the tag,
        or the rule's description names another owner."""
        owner = self._owner_cache or ""
        users = [i for i in api.find_instances(self._workshop_filter(workshop))
                 if i["name"] not in gone]
        results: list[DeprovisionResult] = []
        for name in sorted(names):
            fw = api.get_firewall(name)
            if fw is None:
                continue
            fw_owner, _ = _fw_owner(fw.get("description", ""))
            if fw_owner and fw_owner != owner:
                continue
            if any(name in (i.get("tags") or []) for i in users):
                continue
            try:
                api.delete_firewall(name)
                results.append(DeprovisionResult(id="firewall", ok=True, provider_id=name,
                                                 detail="deleted"))
            except Exception as exc:  # noqa: BLE001
                results.append(DeprovisionResult(id="firewall", ok=True, provider_id=name,
                                                 detail=f"delete failed, re-run destroy: {exc}"))
        return results

    # -- ownership -------------------------------------------------------------
    @staticmethod
    def _workshop_filter(workshop: str) -> str:
        return (f'labels.{LABEL_MANAGED_BY} = "{TAG_MANAGED_BY_VALUE}" AND '
                f'labels.{LABEL_WORKSHOP} = "{label_value(workshop)}"')

    @staticmethod
    def _deployment_filter(deployment: str) -> str:
        return (f'labels.{LABEL_MANAGED_BY} = "{TAG_MANAGED_BY_VALUE}" AND '
                f'labels.{LABEL_DEPLOYMENT} = "{deployment}"')

    def _mine(self, api: Any, workshop: str, key: LabKey | None) -> list[dict[str, Any]]:
        """Live instances of this caller's deployment of ``workshop`` (see ``is_mine``)."""
        if key is None:
            return []
        owner = self._owner_cache or ""
        found: dict[str, dict[str, Any]] = {}
        if key.deployment:
            for inst in api.find_instances(self._deployment_filter(key.deployment)):
                found[f"{inst['zone']}/{inst['name']}"] = inst
        for inst in api.find_instances(self._workshop_filter(workshop)):
            found.setdefault(f"{inst['zone']}/{inst['name']}", inst)
        return [i for i in found.values() if is_mine(i, workshop=workshop, key=key, owner=owner)]

    def _adopt(self, api: Any, key: LabKey, lab: list[dict[str, Any]]) -> None:
        """Label this caller's instances from before issue #175 (matched by the lab key)."""
        from .aws import _rodeo_version

        owner = self._owner_cache or ""
        new = {LABEL_OWNER: owner_label(owner), LABEL_DEPLOYMENT: self._deployment(key),
               LABEL_VERSION: label_value(_rodeo_version())}
        for inst in lab:
            if inst["labels"].get(LABEL_DEPLOYMENT):
                continue
            api.set_labels(inst["zone"], inst["name"], new)
            inst["labels"] = {**inst["labels"], **new}

    def _lab_key(self, api: Any, workshop: str) -> tuple[LabKey, list[dict[str, Any]]]:
        """This deployment's SSH key and live instances: kept while this caller's lab has
        instances (older ones adopted), else a fresh key and deployment id. A teammate's
        lab with the same name is not ours and does not block this."""
        self.owner()
        key = load_lab_ssh_key(workshop)
        mine = self._mine(api, workshop, key)
        if mine and key is not None:
            self._adopt(api, key, mine)
            return key, mine
        return new_lab_ssh_key(workshop), []

    def _recorded(self, api: Any, spec: ProvisionSpec) -> list[dict[str, Any]]:
        """Destroy without the lab key: only instance names this machine recorded."""
        names = {n for n in spec.known_instance_ids if n}
        if not names:
            raise ConfigError(
                f"lab {spec.workshop!r} has no SSH key on this machine and no recorded "
                "instance, so rodeo cannot tell which instances are yours. List your "
                "deployments with `rodeo cloud ls --provider gcp` and remove one with "
                "`rodeo cloud destroy --provider gcp <deployment>`."
            )
        owner = self._owner_cache or ""
        out = []
        for inst in api.find_instances(self._workshop_filter(spec.workshop)):
            if inst["name"] not in names:
                continue
            if inst["metadata"].get(META_WORKSHOP, spec.workshop) != spec.workshop:
                continue
            label = inst["labels"].get(LABEL_OWNER)
            if label is not None and label != owner_label(owner):
                continue
            out.append(inst)
        return out

    # -- deprovision -----------------------------------------------------------
    def deprovision(self, spec: ProvisionSpec, config: dict[str, Any]) -> list[DeprovisionResult]:
        """Delete this caller's deployment of ``spec.workshop``, never a teammate's lab
        with the same name (same rule as the AWS provider)."""
        self.validate({**config, "instance_tier": config.get("instance_tier") or "recommended"})
        api = self._client(config)
        self.owner(config)
        key = load_lab_ssh_key(spec.workshop)
        if key is not None:
            lab = self._mine(api, spec.workshop, key)
            self._adopt(api, key, lab)
        else:
            lab = self._recorded(api, spec)
        want = {label_value(h) for h in spec.host_ids} if spec.host_ids else None
        targets = [i for i in lab if want is None or i["labels"].get(LABEL_HOST_ID) in want]
        results: list[DeprovisionResult] = []
        for inst in targets:
            hid = inst["labels"].get(LABEL_HOST_ID) or inst["name"]
            try:
                api.delete_instance(inst["zone"], inst["name"])
                results.append(DeprovisionResult(id=hid, ok=True, provider_id=inst["name"],
                                                 detail="deleted"))
            except Exception as exc:  # noqa: BLE001
                results.append(DeprovisionResult(id=hid, ok=False, error=str(exc),
                                                 provider_id=inst["name"]))
        if not targets:
            results = [DeprovisionResult(id=h, ok=True, detail="no matching instance")
                       for h in spec.host_ids] or [DeprovisionResult(id="*", ok=True,
                                                                     detail="no matching instances")]
        gone = {i["name"] for i in targets}
        left = [i for i in (self._mine(api, spec.workshop, key) if key else lab)
                if i["name"] not in gone]
        if all(r.ok for r in results) and not left:
            names = {t for i in lab for t in i.get("tags") or [] if str(t).startswith("rodeo-")}
            if key is not None:
                names.add(workshop_firewall(spec.workshop, self._deployment(key)))
            results += self._delete_firewalls(api, names, spec.workshop, gone)
            if key is not None:
                delete_lab_ssh_key(spec.workshop)
        return results

    # -- rodeo cloud (issue #175) ----------------------------------------------
    def list_deployments(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        """Every rodeo instance in the project, grouped by deployment (``legacy`` without one)."""
        api = self._client(config)
        groups: dict[str, dict[str, Any]] = {}
        for inst in api.find_instances(f'labels.{LABEL_MANAGED_BY} = "{TAG_MANAGED_BY_VALUE}"'):
            labels, meta = inst["labels"], inst["metadata"]
            if labels.get(LABEL_MANAGED_BY) != TAG_MANAGED_BY_VALUE:
                continue
            dep = labels.get(LABEL_DEPLOYMENT, "")
            ssh_keys = str(meta.get("ssh-keys") or "")
            gid = dep or f"legacy:{labels.get(LABEL_WORKSHOP, '')}:{ssh_keys}"
            row = groups.setdefault(gid, {
                "deployment": dep, "legacy": not dep,
                "workshop": meta.get(META_WORKSHOP) or labels.get(LABEL_WORKSHOP, ""),
                "owner": meta.get(META_OWNER) or labels.get(LABEL_OWNER, ""),
                "key_name": "", "ssh_keys": ssh_keys, "hosts": [], "instances": [],
                "states": [], "launched": "", "expires": "",
            })
            row["hosts"].append(labels.get(LABEL_HOST_ID) or inst["name"])
            row["instances"].append(inst["name"])
            row["states"].append(str(inst.get("status") or "").lower())
            created = str(inst.get("created") or "")
            if created and (not row["launched"] or created < row["launched"]):
                row["launched"] = created
            expiry = labels.get(LABEL_EXPIRES_AT, "")
            if expiry.isdigit():
                from datetime import datetime, timezone

                expiry = deadman.iso(datetime.fromtimestamp(int(expiry), tz=timezone.utc))
            if expiry and (not row["expires"] or expiry > row["expires"]):
                row["expires"] = expiry
        return sorted(groups.values(), key=lambda r: (r["workshop"], r["deployment"]))

    def destroy_deployment(
        self, config: dict[str, Any], deployment: str, *, allow_foreign: bool = False,
    ) -> list[DeprovisionResult]:
        """Delete every instance and firewall rule of one deployment."""
        api = self._client(config)
        owner = self.owner(config)
        insts = [i for i in api.find_instances(self._deployment_filter(deployment))
                 if i["labels"].get(LABEL_DEPLOYMENT) == deployment
                 and i["labels"].get(LABEL_MANAGED_BY) == TAG_MANAGED_BY_VALUE]
        fws = [fw for fw in api.list_firewalls() if _fw_owner(fw["description"])[1] == deployment]
        owners = {i["metadata"].get(META_OWNER) or i["labels"].get(LABEL_OWNER, "") for i in insts}
        owners |= {_fw_owner(fw["description"])[0] for fw in fws}
        if not owners:
            return [DeprovisionResult(id=deployment, ok=True, detail="nothing found")]
        foreign = sorted(o for o in owners if o != owner)
        if foreign and not allow_foreign:
            raise ConfigError(
                f"deployment {deployment} belongs to {', '.join(foreign)}, not to you "
                f"({owner}). Pass --not-mine to remove it anyway."
            )
        results: list[DeprovisionResult] = []
        for inst in insts:
            api.delete_instance(inst["zone"], inst["name"])
            results.append(DeprovisionResult(id=inst["labels"].get(LABEL_HOST_ID) or inst["name"],
                                             ok=True, provider_id=inst["name"], detail="deleted"))
        for fw in fws:
            try:
                api.delete_firewall(fw["name"])
                detail = "deleted"
            except Exception as exc:  # noqa: BLE001
                detail = f"delete failed, re-run: {exc}"
            results.append(DeprovisionResult(id="firewall", ok=True, provider_id=fw["name"],
                                             detail=detail))
        return results
