"""rodeo fleet preflight: will this workshop fit, before anything is created.

Read-only AWS calls. Checks what has stopped big runs before: the account's
vCPU quota for the instance family, the instance type in the subnet's AZ, the
laptop's AWS session outliving the deploy and, for a player workshop, the DNS
zone and the event certificate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..config import ConfigError
from .inventory import FleetInventory, desired_host_ids, require_provider

# EC2 "Running On-Demand ... instances" quotas (vCPUs), by family bucket.
QUOTA_CODES = {
    "standard": "L-1216C47A",  # A, C, D, H, I, M, R, T, Z
    "g": "L-DB2E81BA",  # G and VT
    "p": "L-417A185B",
    "x": "L-7295265B",
    "f": "L-74FC7D96",
    "inf": "L-1945791B",
    "dl": "L-6E869C2A",
    "trn": "L-2C3B7624",
    "hpc": "L-F7808C92",
}
DEPLOY_SECONDS = 3600  # about 40 min of deploy plus images and player


@dataclass
class Check:
    name: str
    state: str  # ok | warn | fail
    detail: str


def quota_bucket(instance_type: str) -> str:
    """m8id.8xlarge -> standard, g6.xlarge -> g, inf2.xlarge -> inf."""
    family = re.match(r"[a-z]+", instance_type.split(".", 1)[0].lower())
    letters = family.group(0) if family else ""
    for multi in ("inf", "trn", "hpc", "dl"):
        if letters.startswith(multi):
            return multi
    if letters.startswith(("g", "vt")):
        return "g"
    return letters[:1] if letters[:1] in ("p", "x", "f") else "standard"


def planned_instance_type(inventory: FleetInventory, provider: dict[str, Any]) -> str:
    explicit = str(provider.get("instance_type") or "").strip()
    if explicit:
        return explicit
    if not inventory.lab_profile:
        raise ConfigError("set provider.instance_type, or lab.profile so the tier can pick one")
    from ..providers.instance_catalog import resolve_instance_type

    itype, _ = resolve_instance_type(profile=inventory.lab_profile, instance_type=None,
                                     instance_tier=str(provider.get("instance_tier") or "") or None)
    return itype


def hosts_to_create(inventory: FleetInventory, count: int | None) -> int:
    """New hosts the next provision makes (hosts already provisioned don't count)."""
    existing = sum(1 for h in inventory.hosts if h.labels.get("provider_id"))
    total = count if count is not None else len(desired_host_ids(inventory))
    return max(total - existing, 0)


def _vcpus(ec2: Any, types: set[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    todo = sorted(types)
    for i in range(0, len(todo), 100):
        for t in ec2.describe_instance_types(InstanceTypes=todo[i:i + 100])["InstanceTypes"]:
            out[t["InstanceType"]] = int(t["VCpuInfo"]["DefaultVCpus"])
    return out


def _running_types(ec2: Any) -> list[str]:
    types: list[str] = []
    for page in ec2.get_paginator("describe_instances").paginate(
            Filters=[{"Name": "instance-state-name", "Values": ["pending", "running"]}]):
        for r in page["Reservations"]:
            types += [i["InstanceType"] for i in r["Instances"]]
    return types


def check_quota(ec2: Any, quotas: Any, itype: str, new_hosts: int, extra: dict[str, int]) -> Check:
    """Free vCPUs in the family bucket against what the new hosts need.

    *extra* are other instances the run adds (the portal machine), type -> count.
    """
    bucket = quota_bucket(itype)
    running = _running_types(ec2)
    sizes = _vcpus(ec2, {itype, *extra, *running})
    need = new_hosts * sizes[itype] + sum(sizes[t] * n for t, n in extra.items() if quota_bucket(t) == bucket)
    used = sum(sizes[t] for t in running if quota_bucket(t) == bucket)
    try:
        limit = int(quotas.get_service_quota(ServiceCode="ec2", QuotaCode=QUOTA_CODES[bucket])["Quota"]["Value"])
    except Exception as exc:  # noqa: BLE001 - no servicequotas permission is a warning, not a stop
        return Check("vCPU quota", "warn", f"could not read the {bucket} quota ({exc.__class__.__name__}); "
                     f"this run needs {need} vCPUs")
    free = limit - used
    detail = f"{bucket} family: need {need}, free {free} (quota {limit}, in use {used})"
    if need > free:
        return Check("vCPU quota", "fail", detail + ": ask AWS for an increase (Service Quotas, "
                     f"{QUOTA_CODES[bucket]}) or use fewer or smaller hosts")
    return Check("vCPU quota", "warn" if need > 0.8 * free else "ok", detail)


def check_offering(ec2: Any, subnet_id: str, itype: str) -> Check:
    az = ec2.describe_subnets(SubnetIds=[subnet_id])["Subnets"][0]["AvailabilityZone"]
    offered = ec2.describe_instance_type_offerings(
        LocationType="availability-zone",
        Filters=[{"Name": "location", "Values": [az]}, {"Name": "instance-type", "Values": [itype]}],
    )["InstanceTypeOfferings"]
    if offered:
        return Check("instance type", "ok", f"{itype} offered in {az}")
    return Check("instance type", "fail", f"{itype} is not offered in {az} ({subnet_id}): pick a subnet in "
                 "another AZ or another instance type")


def check_session(seconds_left: float | None, refreshable: bool = False) -> Check:
    """API calls renew refreshable credentials (`aws login`, SSO) on their own;
    fixed ones must outlive the deploy."""
    if seconds_left is None:
        return Check("AWS session", "ok", "credentials do not expire")
    if refreshable:
        return Check("AWS session", "ok", "renewed on use (aws login / SSO); keep the login session "
                     "alive for the whole run")
    minutes = int(seconds_left // 60)
    if seconds_left < DEPLOY_SECONDS:
        return Check("AWS session", "warn", f"expires in {minutes} min, a run takes about 60: "
                     "renew the credentials right before you start")
    return Check("AWS session", "ok", f"{minutes} min left")


def session_info() -> tuple[float | None, bool]:
    """(seconds the current credentials live, whether boto3 renews them)."""
    import boto3

    from .lab_images import credential_seconds_left

    creds = boto3.Session().get_credentials()
    return credential_seconds_left(), type(creds).__name__ == "RefreshableCredentials" and \
        getattr(creds, "method", "") in ("login", "sso", "assume-role", "assume-role-with-web-identity")


def check_player(inventory: FleetInventory, route53: Any) -> list[Check]:
    if inventory.player is None:
        return []
    from . import cert, dns

    domain = inventory.player.domain
    out: list[Check] = []
    try:
        zone = dns.find_zone(route53, domain)
        out.append(Check("DNS zone", "ok", f"{domain} ({zone})"))
    except ConfigError as exc:
        out.append(Check("DNS zone", "fail", str(exc)))
    files = cert.current(domain, inventory.player.staging)
    if files is None:
        out.append(Check("certificate", "warn", f"no *.{domain} yet: `fleet cert` (or `fleet up`) issues it"))
    elif files.days_left < cert.MIN_DAYS_LEFT:
        out.append(Check("certificate", "warn", f"*.{domain} has {files.days_left:.0f} days left: "
                         "`fleet cert` renews it"))
    else:
        out.append(Check("certificate", "ok", f"*.{domain}, {files.days_left:.0f} days left"))
    return out


def preflight(inventory: FleetInventory, *, count: int | None = None, ec2: Any = None, quotas: Any = None,
              route53: Any = None, session: Any = None) -> list[Check]:
    """All checks for an AWS workshop. Clients are created when not given."""
    provider = require_provider(inventory)
    if str(provider.get("type")) != "aws":
        raise ConfigError("fleet preflight checks AWS workshops (provider.type: aws)")
    region = str(provider.get("region") or "")
    subnet = str(provider.get("subnet_id") or "")
    if not region or not subnet:
        raise ConfigError("provider.region and provider.subnet_id are needed (`fleet up` writes them)")
    if ec2 is None or quotas is None:
        import boto3  # the [aws] extra; tests pass fakes

        ec2 = ec2 or boto3.client("ec2", region_name=region)
        quotas = quotas or boto3.client("service-quotas", region_name=region)
    session = session or session_info
    itype = planned_instance_type(inventory, provider)
    new_hosts = hosts_to_create(inventory, count)
    extra: dict[str, int] = {}
    portal = inventory.portal
    if portal and portal.enabled and not portal.host and not portal.provider_id:
        from ..providers.aws import DEFAULT_PORTAL_INSTANCE_TYPE

        extra[portal.instance_type or DEFAULT_PORTAL_INSTANCE_TYPE] = 1
    checks = [
        check_session(*session()),
        check_offering(ec2, subnet, itype),
        check_quota(ec2, quotas, itype, new_hosts, extra),
    ]
    if inventory.player is not None:
        from . import dns

        checks += check_player(inventory, route53 or dns.route53_client())
    return checks
