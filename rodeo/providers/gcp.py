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
- Firewall. One rule per workshop, ``rodeo-<workshop>``, targeting the network tag of the
  same name, scoped to this machine's public IP (like the AWS managed security group).
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
    TAG_HOST_ID,
    TAG_MANAGED_BY_VALUE,
    TAG_WORKSHOP,
    DeprovisionResult,
    ProvisionedHost,
    ProvisionSpec,
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

LABEL_MANAGED_BY = "managed-by"
LABEL_WORKSHOP = TAG_WORKSHOP  # "rodeo-workshop": already a valid label key
LABEL_HOST_ID = TAG_HOST_ID  # "rodeo-host-id"
LABEL_EXPIRES_AT = "rodeo-expires-at"  # unix seconds; ISO has ':' which labels forbid
META_WORKSHOP = "rodeo-workshop"  # exact (unsanitised) workshop name

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


def workshop_firewall(workshop: str) -> str:
    return resource_name(f"rodeo-{workshop}")


def instance_name(workshop: str, host_id: str) -> str:
    return resource_name(f"rodeo-{workshop}-{host_id}")


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
        }

    # -- writes ----------------------------------------------------------------
    def insert_instance(self, zone: str, body: dict[str, Any]) -> None:
        inst = self.c.Instance(body)
        self.instances.insert(project=self.project, zone=zone, instance_resource=inst).result(timeout=600)

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
        return {"name": fw.name, "source_ranges": sorted(fw.source_ranges or [])}

    def upsert_firewall(self, name: str, network: str, ports: tuple[int, ...],
                        source_ranges: list[str], tag: str, exists: bool) -> None:
        fw = self.c.Firewall(
            name=name,
            network=f"projects/{self.project}/global/networks/{network}",
            direction="INGRESS",
            source_ranges=source_ranges,
            target_tags=[tag],
            allowed=[self.c.Allowed(I_p_protocol="tcp", ports=[str(p) for p in ports])],
            description="rodeo-cli lab access (managed by rodeo)",
        )
        if exists:
            self.firewalls.patch(project=self.project, firewall=name,
                                 firewall_resource=fw).result(timeout=300)
        else:
            self.firewalls.insert(project=self.project, firewall_resource=fw).result(timeout=300)

    def delete_firewall(self, name: str) -> None:
        self.firewalls.delete(project=self.project, firewall=name).result(timeout=300)


def _gcloud_config_project() -> str | None:
    """``gcloud config get-value project``; gcloud is often installed off PATH."""
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
        out = subprocess.run([gcloud, "config", "get-value", "project"], capture_output=True,
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
    ) -> None:
        self._api = api
        self._sleep = sleep
        self._caller_ip_fn = caller_ip_fn

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
        cfg.setdefault("region", default_region())
        api = self._client(cfg)
        lab_key = self._lab_key(api, spec.workshop)
        ssh_user = spec.ssh_user or DEFAULT_SSH_USER
        wait_spec = ProvisionSpec(
            workshop=spec.workshop, host_ids=spec.host_ids, ssh_user=ssh_user,
            identity_file=str(lab_key.private), extra_labels=spec.extra_labels,
            wait_ssh=spec.wait_ssh, ssh_timeout=spec.ssh_timeout,
        )
        existing = {h: self._find_owned(api, spec.workshop, h) for h in spec.host_ids}
        to_create = [h for h, inst in existing.items() if inst is None]
        zones = self.candidate_zones(api, cfg) if to_create else []
        net, sub = self.network_names(cfg)
        if not (cfg.get("network") or cfg.get("subnetwork")):
            api.ensure_network(net, str(cfg["region"]), sub, SUBNET_RANGE)
        self._reconcile_firewall(api, cfg, net, spec.workshop)
        image = self._image(api, cfg) if to_create else ""

        out: list[ProvisionedHost] = []
        for host_id in spec.host_ids:
            inst = existing[host_id]
            if inst is None:
                inst = self._create(api, wait_spec, cfg, host_id, zones, image, lab_key)
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
                      **(cfg.get("labels") or {})}
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
        lab_host: bool = True,
    ) -> dict[str, Any]:
        """The Instance resource (as a dict) for one lab host."""
        itype = str(cfg["instance_type"])
        region = zone.rsplit("-", 1)[0]
        _, sub = self.network_names({**cfg, "region": region})
        labels: dict[str, str] = {}
        for k, v in {**(cfg.get("labels") or {}), **(extra_labels or {})}.items():
            key, val = label_value(k), label_value(v)
            if key and key[0].isalpha():
                labels[key] = val
        # Ownership labels last so nothing above can replace them: deprovision finds
        # instances by these, and a lost one would leak a billing VM.
        labels.update({
            LABEL_MANAGED_BY: TAG_MANAGED_BY_VALUE,
            LABEL_WORKSHOP: label_value(workshop),
            LABEL_HOST_ID: label_value(host_id),
        })
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
            "name": instance_name(workshop, host_id),
            "machine_type": f"zones/{zone}/machineTypes/{itype}",
            "labels": labels,
            "tags": {"items": [workshop_firewall(workshop)]},
            "metadata": {"items": metadata},
            "network_interfaces": [nic],
            "disks": [{
                "boot": True,
                "auto_delete": True,
                "initialize_params": {
                    "source_image": image,
                    "disk_size_gb": boot_gb,
                    "disk_type": f"zones/{zone}/diskTypes/{boot_disk_type(itype)}",
                    "labels": {LABEL_MANAGED_BY: TAG_MANAGED_BY_VALUE,
                               LABEL_WORKSHOP: label_value(workshop)},
                },
            }],
            "scheduling": scheduling,
            "advanced_machine_features": {"enable_nested_virtualization": True},
        }
        return body

    def _create(self, api: Any, spec: ProvisionSpec, cfg: dict[str, Any], host_id: str,
                zones: list[str], image: str, key: LabKey) -> dict[str, Any]:
        last: BaseException | None = None
        for zone in zones:
            body = self.instance_body(
                cfg, project=str(api.project), workshop=spec.workshop, host_id=host_id,
                zone=zone, image=image, ssh_user=spec.ssh_user,
                public_key=key.public.read_text(), extra_labels=spec.extra_labels,
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
    def _reconcile_firewall(self, api: Any, cfg: dict[str, Any], network: str, workshop: str) -> None:
        cidrs = cfg.get("allowed_cidrs")
        if not cidrs:
            ip = self._caller_ip_fn()
            cidrs = [f"{ip}/32" if ":" not in ip else f"{ip}/128"]
        name = workshop_firewall(workshop)
        current = api.get_firewall(name)
        want = sorted(str(c) for c in cidrs)
        if current is not None and current["source_ranges"] == want:
            return
        api.upsert_firewall(name, network, MANAGED_FW_PORTS, want, name, exists=current is not None)

    # -- ownership -------------------------------------------------------------
    @staticmethod
    def _filter(workshop: str) -> str:
        return (f'labels.{LABEL_MANAGED_BY} = "{TAG_MANAGED_BY_VALUE}" AND '
                f'labels.{LABEL_WORKSHOP} = "{label_value(workshop)}"')

    def _owned(self, api: Any, workshop: str) -> list[dict[str, Any]]:
        """Live instances of exactly this workshop (label match + metadata name)."""
        out = []
        for inst in api.find_instances(self._filter(workshop)):
            if inst["labels"].get(LABEL_MANAGED_BY) != TAG_MANAGED_BY_VALUE:
                continue
            if inst["metadata"].get(META_WORKSHOP, workshop) != workshop:
                continue  # another workshop whose name sanitises the same
            out.append(inst)
        return out

    def _find_owned(self, api: Any, workshop: str, host_id: str) -> dict[str, Any] | None:
        for inst in self._owned(api, workshop):
            if inst["labels"].get(LABEL_HOST_ID) == label_value(host_id):
                return inst
        return None

    def _lab_key(self, api: Any, workshop: str) -> LabKey:
        """This deployment's SSH key: kept while the lab has instances, else a fresh one."""
        key = load_lab_ssh_key(workshop)
        if self._owned(api, workshop):
            if key is None:
                raise ConfigError(
                    f"lab {workshop!r} already has instances in GCP whose private key is not "
                    "on this machine (deployed from elsewhere). Give your plan a different "
                    "name, or destroy that lab from where it was deployed."
                )
            return key
        return new_lab_ssh_key(workshop)

    # -- deprovision -----------------------------------------------------------
    def deprovision(self, spec: ProvisionSpec, config: dict[str, Any]) -> list[DeprovisionResult]:
        self.validate({**config, "instance_tier": config.get("instance_tier") or "recommended"})
        api = self._client(config)
        want = {label_value(h) for h in spec.host_ids} if spec.host_ids else None
        targets = [i for i in self._owned(api, spec.workshop)
                   if want is None or i["labels"].get(LABEL_HOST_ID) in want]
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
        if all(r.ok for r in results) and not self._owned(api, spec.workshop):
            name = workshop_firewall(spec.workshop)
            if api.get_firewall(name) is not None:
                try:
                    api.delete_firewall(name)
                    results.append(DeprovisionResult(id="firewall", ok=True, provider_id=name,
                                                     detail="deleted"))
                except Exception as exc:  # noqa: BLE001
                    results.append(DeprovisionResult(id="firewall", ok=True, provider_id=name,
                                                     detail=f"delete failed, re-run destroy: {exc}"))
            delete_lab_ssh_key(spec.workshop)
        return results
