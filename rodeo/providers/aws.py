"""AWS EC2 HostProvider for Fleet F4a."""
from __future__ import annotations

import time
from typing import Any, Callable

from ..config import ConfigError
from ..fleet.inventory import FleetHost, FleetInventory
from ..fleet.ssh_exec import run_remote
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

_REQUIRED = ("region", "instance_type", "ami", "key_name", "subnet_id", "security_group_ids")

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
        sgs = config["security_group_ids"]
        if not isinstance(sgs, list) or not sgs or not all(isinstance(x, str) for x in sgs):
            raise ConfigError("provider.security_group_ids must be a non-empty list of strings")
        if int(config.get("volume_size_gib") or 0) < 0:
            raise ConfigError("provider.volume_size_gib must be >= 0")
        itype = str(config["instance_type"]).lower()
        if any(itype.startswith(p) or itype == p.rstrip(".") for p in _TOO_SMALL_PREFIXES):
            raise ConfigError(
                f"provider.instance_type {config['instance_type']!r} is too small "
                "for nested Harvester/Edge labs — use metal (e.g. m7i.metal-24xl) "
                "or a large Nitro nested-virt type (≥128 GiB RAM recommended) "
                "with nested_virtualization: true"
            )

    def provision(
        self,
        spec: ProvisionSpec,
        config: dict[str, Any],
    ) -> list[ProvisionedHost]:
        self.validate(config)
        ec2 = self._client(config)
        out: list[ProvisionedHost] = []
        for host_id in spec.host_ids:
            existing = self._find_owned(ec2, spec.workshop, host_id)
            if existing:
                inst = existing
                action = "reuse"
                if inst["State"]["Name"] in ("stopped", "stopping"):
                    ec2.start_instances(InstanceIds=[inst["InstanceId"]])
            else:
                inst = self._create(ec2, spec, config, host_id)
                action = "create"
            inst = self._wait_running(ec2, inst["InstanceId"], timeout=float(config.get("wait_timeout") or 600))
            public_ip = (inst.get("PublicIpAddress") or "").strip()
            if not public_ip:
                raise ConfigError(
                    f"AWS instance {inst['InstanceId']} ({host_id}) has no public IP — "
                    "enable associate_public_ip or use a subnet that assigns one"
                )
            labels = {
                "provider": "aws",
                **spec.extra_labels,
                **(config.get("labels") or {}),
            }
            labels = {str(k): str(v) for k, v in labels.items()}
            host = ProvisionedHost(
                id=host_id,
                ssh=public_ip,
                public_ip=public_ip,
                labels=labels,
                provider_id=inst["InstanceId"],
            )
            if spec.wait_ssh:
                self._wait_ssh(spec, host, timeout=spec.ssh_timeout)
            # annotate reuse/create for callers via labels (non-destructive)
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
        tags = ownership_tags(spec.workshop, host_id)
        tags["Name"] = f"{spec.workshop}-{host_id}"
        for k, v in (config.get("labels") or {}).items():
            tags[str(k)] = str(v)
        for k, v in spec.extra_labels.items():
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
            "KeyName": str(config["key_name"]),
            "MinCount": 1,
            "MaxCount": 1,
            "NetworkInterfaces": [ni],
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [{"Key": k, "Value": v} for k, v in tags.items()],
                }
            ],
        }
        vol = int(config.get("volume_size_gib") or 0)
        if vol > 0:
            kwargs["BlockDeviceMappings"] = [
                {
                    "DeviceName": "/dev/sda1",
                    "Ebs": {"VolumeSize": vol, "VolumeType": "gp3", "DeleteOnTermination": True},
                }
            ]
        if config.get("nested_virtualization"):
            kwargs["CpuOptions"] = {"NestedVirtualization": "enabled"}

        resp = ec2.run_instances(**kwargs)
        inst = resp["Instances"][0]
        return inst

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
        while time.monotonic() < deadline:
            result = run_remote(inventory, fh, ["true"], timeout=20.0)
            if result.ok:
                return
            last_err = (result.stderr or result.stdout or f"exit {result.rc}").strip()
            self._sleep(10)
        raise ConfigError(f"SSH not ready on {host.id} ({host.public_ip}): {last_err[:200]}")
