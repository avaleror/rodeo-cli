"""AWS EC2 HostProvider for Fleet F4a."""
from __future__ import annotations

import time
from typing import Any, Callable

from ..config import ConfigError
from ..fleet.inventory import FleetHost, FleetInventory
from ..fleet.ssh_exec import run_remote
from ..ssh_key import (
    DEFAULT_EC2_KEY_NAME,
    build_ec2_userdata,
    ensure_ec2_key_pair,
    plant_rodeo_ssh_key,
    resolve_ssh_identity,
)
from .base import (
    TAG_HOST_ID,
    TAG_MANAGED_BY,
    TAG_MANAGED_BY_VALUE,
    TAG_WORKSHOP,
    DeprovisionResult,
    ProvisionedHost,
    ProvisionSpec,
    ownership_tags,
)

_REQUIRED = ("region", "subnet_id", "security_group_ids")

# Recommended default for performance-first Harvester labs (local NVMe).
DEFAULT_INSTANCE_TYPE = "i7i.8xlarge"

# Prefer openSUSE Leap 16 (Marketplace) — same family as SLES 16, free, ssh as ec2-user.
# Pin with provider.ami when you need a fixed image; otherwise resolve newest match.
DEFAULT_AMI_NAME_FILTER = "openSUSE Leap 16.0 (x86_64)*"
DEFAULT_AMI_OWNERS = ("aws-marketplace",)

# Instance type prefixes that cannot host full Harvester / Edge nested labs.
_TOO_SMALL_PREFIXES = (
    "t2.",
    "t3.",
    "t3a.",
    "t4g.",
    "m5.large",
    "m5.xlarge",
    "m6i.large",
    "m6i.xlarge",
    "m7i.large",
    "m7i.xlarge",
    "c5.large",
    "c5.xlarge",
    "c6i.large",
    "c6i.xlarge",
    "c7i.large",
    "c7i.xlarge",
)


def _aws_error_parts(exc: BaseException) -> tuple[str, str]:
    """Extract (Code, Message) from a boto ClientError or generic Exception."""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        err = resp.get("Error") or {}
        if isinstance(err, dict):
            return str(err.get("Code") or ""), str(err.get("Message") or exc)
    return "", str(exc)


def _require_boto3():
    try:
        import boto3  # noqa: F401
        import botocore  # noqa: F401
    except ImportError as exc:
        raise ConfigError(
            "AWS provider requires boto3 — install with: pip install 'rodeo-cli[aws]'"
        ) from exc


class AwsHostProvider:
    """Provision/reuse/terminate EC2 instances tagged for a workshop."""

    name = "aws"

    def __init__(
        self,
        *,
        ec2_client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._ec2 = ec2_client
        self._sleep = sleep

    def _client(self, config: dict[str, Any]) -> Any:
        if self._ec2 is not None:
            return self._ec2
        _require_boto3()
        import boto3

        return boto3.client("ec2", region_name=str(config["region"]))

    def validate(self, config: dict[str, Any]) -> None:
        if str(config.get("type") or "").lower() != "aws":
            raise ConfigError("AwsHostProvider requires provider.type: aws")
        for key in _REQUIRED:
            if key not in config or config[key] in (None, "", []):
                raise ConfigError(f"provider.{key} is required for AWS")
        itype = str(config.get("instance_type") or "").strip()
        tier = str(config.get("instance_tier") or "").strip()
        if not itype and not tier:
            raise ConfigError(
                "provider.instance_type or provider.instance_tier is required for AWS "
                "(tiers: budget | recommended | performance)"
            )
        if tier:
            from .instance_catalog import normalize_tier

            normalize_tier(tier)
        ami = str(config.get("ami") or "").strip()
        filt = str(config.get("ami_name_filter") or "").strip()
        if not ami and not filt:
            # OK — provision will use DEFAULT_AMI_NAME_FILTER (Leap 16).
            pass
        sgs = config["security_group_ids"]
        if not isinstance(sgs, list) or not sgs or not all(isinstance(x, str) for x in sgs):
            raise ConfigError("provider.security_group_ids must be a non-empty list of strings")
        owners = config.get("ami_owners")
        if owners is not None and (
            not isinstance(owners, list) or not all(isinstance(x, str) for x in owners)
        ):
            raise ConfigError("provider.ami_owners must be a list of strings")
        if int(config.get("volume_size_gib") or 0) < 0:
            raise ConfigError("provider.volume_size_gib must be >= 0")
        if itype:
            self._reject_too_small(itype)
            # Nested virt is required on virtual (non-metal) types for KVM guests.
            if "metal" not in itype.lower() and config.get("nested_virtualization") is False:
                raise ConfigError(
                    "provider.nested_virtualization: false is only valid on metal "
                    "instance types — virtual Nitro types need NestedVirtualization=enabled"
                )

    @staticmethod
    def _reject_too_small(itype: str) -> None:
        key = itype.lower()
        if any(key.startswith(p) or key == p.rstrip(".") for p in _TOO_SMALL_PREFIXES):
            raise ConfigError(
                f"provider.instance_type {itype!r} is too small "
                "for nested Harvester/Edge labs — prefer i7i.8xlarge (local NVMe) "
                "or metal (e.g. m7i.metal-24xl) / large Nitro (≥128 GiB RAM) "
                "with nested_virtualization: true"
            )

    def apply_instance_selection(
        self,
        config: dict[str, Any],
        *,
        profile: str,
    ) -> dict[str, Any]:
        """Fill ``instance_type`` from tier/catalog; return updated config copy."""
        from .instance_catalog import resolve_instance_type

        out = dict(config)
        itype, tier = resolve_instance_type(
            profile=profile,
            instance_type=str(out.get("instance_type") or "") or None,
            instance_tier=str(out.get("instance_tier") or "") or None,
        )
        self._reject_too_small(itype)
        out["instance_type"] = itype
        if tier is not None:
            out["instance_tier"] = tier
        return out

    def assert_available(
        self,
        config: dict[str, Any],
        *,
        count: int = 1,
    ) -> None:
        """Fail closed if the instance type is not offered or has no capacity.

        Uses DescribeInstanceTypeOfferings (region) then RunInstances DryRun
        with the resolved AMI / subnet / SGs. ``count`` is reserved for fleet;
        single-host v1 always passes 1.
        """
        if count < 1:
            raise ConfigError("availability count must be >= 1")
        self.validate(config)
        itype = str(config.get("instance_type") or "").strip()
        if not itype:
            raise ConfigError("provider.instance_type is required before availability check")
        region = str(config["region"])
        ec2 = self._client(config)

        offerings = ec2.describe_instance_type_offerings(
            LocationType="region",
            Filters=[{"Name": "instance-type", "Values": [itype]}],
        )
        if not (offerings.get("InstanceTypeOfferings") or []):
            raise ConfigError(
                f"instance type {itype} is not offered in region {region}. "
                "Pick another region (e.g. eu-west-1, us-east-1) or a different "
                "provider.instance_tier / provider.instance_type."
            )

        # Capacity probe: DryRun with real networking + AMI.
        cfg = dict(config)
        cfg["ami"] = self.resolve_ami(ec2, cfg)
        try:
            # MinCount=count would request N; AWS DryRun still validates capacity path.
            kwargs = self._run_instances_kwargs(
                cfg,
                host_id="capacity-probe",
                workshop=str(cfg.get("workshop") or "rodeo-capacity-probe"),
                dry_run=True,
                min_count=count,
                max_count=count,
            )
            ec2.run_instances(**kwargs)
        except Exception as exc:  # noqa: BLE001 — boto ClientError varies by install
            code, message = _aws_error_parts(exc)
            if code == "DryRunOperation":
                return
            if code in (
                "InsufficientInstanceCapacity",
                "InstanceLimitExceeded",
                "MaxSpotInstanceCountExceeded",
                "VcpuLimitExceeded",
                "Unsupported",
            ):
                raise ConfigError(
                    f"not enough capacity for {count}× {itype} in region {region} "
                    f"({code}). Try another region, lower provider.count, or pick a "
                    f"different instance tier/type. AWS said: {message}"
                ) from exc
            # InvalidAMI / auth / bad subnet should surface clearly too
            raise ConfigError(
                f"AWS availability check failed for {itype} in {region} "
                f"({code or type(exc).__name__}): {message}"
            ) from exc

    def resolve_ami(self, ec2: Any, config: dict[str, Any]) -> str:
        """Return ImageId: explicit ``ami`` or newest match for ``ami_name_filter``."""
        ami = str(config.get("ami") or "").strip()
        if ami:
            return ami
        filt = str(config.get("ami_name_filter") or "").strip() or DEFAULT_AMI_NAME_FILTER
        owners = config.get("ami_owners")
        if owners is None:
            owners = list(DEFAULT_AMI_OWNERS)
        resp = ec2.describe_images(
            Owners=list(owners),
            Filters=[
                {"Name": "name", "Values": [filt]},
                {"Name": "state", "Values": ["available"]},
                {"Name": "architecture", "Values": ["x86_64"]},
            ],
        )
        images = list(resp.get("Images") or [])
        if not images:
            raise ConfigError(
                f"no AMI matched ami_name_filter={filt!r} owners={owners!r} — "
                "subscribe to openSUSE Leap on AWS Marketplace once, or set "
                "provider.ami to an explicit ami-… id (SLES 16 / Leap 16)"
            )
        images.sort(key=lambda i: str(i.get("CreationDate") or ""), reverse=True)
        chosen = images[0]
        image_id = str(chosen.get("ImageId") or "").strip()
        if not image_id:
            raise ConfigError(f"matched AMI missing ImageId for filter {filt!r}")
        return image_id

    def provision(
        self,
        spec: ProvisionSpec,
        config: dict[str, Any],
    ) -> list[ProvisionedHost]:
        profile = str(config.get("lab_profile") or config.get("profile") or "harvester")
        cfg = self.apply_instance_selection(config, profile=profile)
        self.validate(cfg)
        ec2 = self._client(cfg)
        cfg = dict(cfg)
        cfg["ami"] = self.resolve_ami(ec2, cfg)
        key_name = str(cfg.get("key_name") or DEFAULT_EC2_KEY_NAME).strip() or DEFAULT_EC2_KEY_NAME
        ensure_ec2_key_pair(ec2, key_name=key_name)
        identity = resolve_ssh_identity(spec.identity_file)
        # Use managed identity for SSH wait even if ProvisionSpec omitted it.
        wait_spec = ProvisionSpec(
            workshop=spec.workshop,
            host_ids=spec.host_ids,
            ssh_user=spec.ssh_user,
            identity_file=identity,
            extra_labels=spec.extra_labels,
            wait_ssh=spec.wait_ssh,
            ssh_timeout=spec.ssh_timeout,
        )
        cfg["key_name"] = key_name
        out: list[ProvisionedHost] = []
        to_create: list[str] = []
        existing_map: dict[str, dict[str, Any]] = {}
        for host_id in wait_spec.host_ids:
            existing = self._find_owned(ec2, wait_spec.workshop, host_id)
            if existing:
                existing_map[host_id] = existing
            else:
                to_create.append(host_id)
        if to_create:
            self.assert_available(cfg, count=len(to_create))
        for host_id in wait_spec.host_ids:
            if host_id in existing_map:
                inst = existing_map[host_id]
                action = "reuse"
                if inst["State"]["Name"] in ("stopped", "stopping"):
                    ec2.start_instances(InstanceIds=[inst["InstanceId"]])
            else:
                inst = self._create(ec2, wait_spec, cfg, host_id)
                action = "create"
            inst = self._wait_running(ec2, inst["InstanceId"], timeout=float(cfg.get("wait_timeout") or 600))
            public_ip = (inst.get("PublicIpAddress") or "").strip()
            if not public_ip:
                raise ConfigError(
                    f"AWS instance {inst['InstanceId']} ({host_id}) has no public IP — "
                    "enable associate_public_ip or use a subnet that assigns one"
                )
            labels = {
                "provider": "aws",
                **wait_spec.extra_labels,
                **(cfg.get("labels") or {}),
            }
            labels = {str(k): str(v) for k, v in labels.items()}
            host = ProvisionedHost(
                id=host_id,
                ssh=public_ip,
                public_ip=public_ip,
                labels=labels,
                provider_id=inst["InstanceId"],
            )
            if wait_spec.wait_ssh:
                self._wait_ssh(wait_spec, host, timeout=wait_spec.ssh_timeout)
                inv = FleetInventory(
                    name=wait_spec.workshop,
                    lab_dir="/",
                    defaults={
                        "ssh_user": wait_spec.ssh_user,
                        "identity_file": identity,
                    },
                    hosts=[],
                )
                fh = FleetHost(id=host.id, ssh=host.ssh, public_ip=host.public_ip)
                plant_rodeo_ssh_key(inv, fh)
            host = ProvisionedHost(
                id=host.id,
                ssh=host.ssh,
                public_ip=host.public_ip,
                labels={**host.labels, "provision_action": action},
                provider_id=host.provider_id,
            )
            out.append(host)
        return out

    def deprovision(
        self,
        spec: ProvisionSpec,
        config: dict[str, Any],
    ) -> list[DeprovisionResult]:
        self.validate(config)
        ec2 = self._client(config)
        # Always scope destroy to workshop tag; optionally limit to host ids
        filters = [
            {"Name": f"tag:{TAG_MANAGED_BY}", "Values": [TAG_MANAGED_BY_VALUE]},
            {"Name": f"tag:{TAG_WORKSHOP}", "Values": [spec.workshop]},
            {
                "Name": "instance-state-name",
                "Values": ["pending", "running", "stopping", "stopped"],
            },
        ]
        resp = ec2.describe_instances(Filters=filters)
        targets: list[tuple[str, str]] = []  # host_id, instance_id
        want = set(spec.host_ids) if spec.host_ids else None
        for res in resp.get("Reservations") or []:
            for inst in res.get("Instances") or []:
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags") or []}
                if tags.get(TAG_MANAGED_BY) != TAG_MANAGED_BY_VALUE:
                    continue
                if tags.get(TAG_WORKSHOP) != spec.workshop:
                    continue
                hid = tags.get(TAG_HOST_ID) or inst["InstanceId"]
                if want is not None and hid not in want:
                    continue
                targets.append((hid, inst["InstanceId"]))

        if not targets:
            return [
                DeprovisionResult(id=hid, ok=True, detail="no matching instance")
                for hid in (spec.host_ids or [])
            ] or [
                DeprovisionResult(id="*", ok=True, detail="no matching instances")
            ]

        ids = [iid for _, iid in targets]
        try:
            ec2.terminate_instances(InstanceIds=ids)
        except Exception as exc:  # noqa: BLE001
            return [
                DeprovisionResult(id=hid, ok=False, error=str(exc), provider_id=iid)
                for hid, iid in targets
            ]
        return [
            DeprovisionResult(id=hid, ok=True, provider_id=iid, detail="terminating")
            for hid, iid in targets
        ]

    def _find_owned(self, ec2: Any, workshop: str, host_id: str) -> dict[str, Any] | None:
        resp = ec2.describe_instances(
            Filters=[
                {"Name": f"tag:{TAG_MANAGED_BY}", "Values": [TAG_MANAGED_BY_VALUE]},
                {"Name": f"tag:{TAG_WORKSHOP}", "Values": [workshop]},
                {"Name": f"tag:{TAG_HOST_ID}", "Values": [host_id]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ]
        )
        for res in resp.get("Reservations") or []:
            for inst in res.get("Instances") or []:
                return inst
        return None

    def _create(
        self,
        ec2: Any,
        spec: ProvisionSpec,
        config: dict[str, Any],
        host_id: str,
    ) -> dict[str, Any]:
        kwargs = self._run_instances_kwargs(
            config,
            host_id=host_id,
            workshop=spec.workshop,
            ssh_user=spec.ssh_user,
            extra_labels=spec.extra_labels,
            dry_run=False,
            min_count=1,
            max_count=1,
        )
        resp = ec2.run_instances(**kwargs)
        return resp["Instances"][0]

    def _run_instances_kwargs(
        self,
        config: dict[str, Any],
        *,
        host_id: str,
        workshop: str,
        ssh_user: str = "ec2-user",
        extra_labels: dict[str, str] | None = None,
        dry_run: bool = False,
        min_count: int = 1,
        max_count: int = 1,
    ) -> dict[str, Any]:
        tags = ownership_tags(workshop, host_id)
        tags["Name"] = f"{workshop}-{host_id}"
        for k, v in (config.get("labels") or {}).items():
            tags[str(k)] = str(v)
        for k, v in (extra_labels or {}).items():
            tags[str(k)] = str(v)

        ni: dict[str, Any] = {
            "DeviceIndex": 0,
            "SubnetId": str(config["subnet_id"]),
            "Groups": list(config["security_group_ids"]),
            "AssociatePublicIpAddress": bool(config.get("associate_public_ip", True)),
        }
        kwargs: dict[str, Any] = {
            "ImageId": str(config["ami"]),
            "InstanceType": str(config["instance_type"]),
            "KeyName": str(config.get("key_name") or DEFAULT_EC2_KEY_NAME),
            "MinCount": min_count,
            "MaxCount": max_count,
            "NetworkInterfaces": [ni],
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [{"Key": k, "Value": v} for k, v in tags.items()],
                }
            ],
            "DryRun": dry_run,
        }
        vol = int(config.get("volume_size_gib") or 0)
        if vol > 0:
            kwargs["BlockDeviceMappings"] = [
                {
                    "DeviceName": "/dev/sda1",
                    "Ebs": {"VolumeSize": vol, "VolumeType": "gp3", "DeleteOnTermination": True},
                }
            ]
        itype = str(config["instance_type"]).lower()
        nested = config.get("nested_virtualization")
        if nested is None:
            nested = "metal" not in itype
        if nested:
            kwargs["CpuOptions"] = {"NestedVirtualization": "enabled"}
        if not dry_run:
            kwargs["UserData"] = build_ec2_userdata(ssh_user=ssh_user)
        return kwargs

    def _wait_running(self, ec2: Any, instance_id: str, *, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            resp = ec2.describe_instances(InstanceIds=[instance_id])
            inst = resp["Reservations"][0]["Instances"][0]
            state = inst["State"]["Name"]
            if state == "running" and inst.get("PublicIpAddress"):
                return inst
            if state in ("terminated", "shutting-down"):
                raise ConfigError(f"AWS instance {instance_id} entered state {state}")
            self._sleep(5)
        raise ConfigError(f"timed out waiting for AWS instance {instance_id} to be running")

    def _wait_ssh(self, spec: ProvisionSpec, host: ProvisionedHost, *, timeout: float) -> None:
        inventory = FleetInventory(
            name=spec.workshop,
            lab_dir="/",
            defaults={
                "ssh_user": spec.ssh_user,
                "identity_file": spec.identity_file,
            },
            hosts=[],
        )
        fh = FleetHost(id=host.id, ssh=host.ssh, public_ip=host.public_ip)
        deadline = time.monotonic() + timeout
        last_err = "no attempt"
        need_sudo = str(spec.ssh_user or "").strip() not in ("", "root")
        while time.monotonic() < deadline:
            result = run_remote(inventory, fh, ["true"], timeout=20.0)
            if result.ok and need_sudo:
                # cloud-init may finish SSH before sudoers drop-in is in place
                sudo_ok = run_remote(inventory, fh, ["sudo", "-n", "true"], timeout=20.0)
                if sudo_ok.ok:
                    return
                last_err = (sudo_ok.stderr or sudo_ok.stdout or "sudo -n not ready").strip()
            elif result.ok:
                return
            else:
                last_err = (result.stderr or result.stdout or f"exit {result.rc}").strip()
            self._sleep(10)
        raise ConfigError(
            f"SSH not ready on {host.id} ({host.public_ip}): {last_err[:200]} "
            "(waiting for login + passwordless sudo from cloud-init)"
        )
