"""AWS EC2 HostProvider for Fleet F4a."""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from ..config import ConfigError
from ..fleet.inventory import STUDENT_SSH_PORT, FleetHost, FleetInventory
from ..fleet.ssh_exec import forget_host_key, run_remote
from ..ssh_key import (
    DEFAULT_EC2_KEY_NAME,
    LabKey,
    build_ec2_userdata,
    delete_ec2_key_pair,
    delete_lab_ssh_key,
    ensure_ec2_key_pair,
    list_lab_ssh_keys,
    load_lab_ssh_key,
    new_lab_ssh_key,
    plant_rodeo_ssh_key,
)
from ..ssh_key import _safe_lab as safe_lab
from . import deadman
from .base import (
    OWNER_ENV,
    ROLE_PORTAL,
    TAG_DEPLOYMENT,
    TAG_HOST_ID,
    TAG_OWNER,
    TAG_ROLE,
    TAG_MANAGED_BY,
    TAG_MANAGED_BY_VALUE,
    TAG_WORKSHOP,
    DeprovisionResult,
    ProvisionedHost,
    ProvisionSpec,
    check_owner,
    deployment_tags,
    ownership_tags,
    reject_reserved_labels,
)

LIVE_STATES = ["pending", "running", "stopping", "stopped"]
TAG_RESOURCE = "rodeo-resource"
LAB_SG_RESOURCE = "security-group"

_REQUIRED = ("region", "subnet_id")

# `rodeo up --target aws` without a provider block: the region comes from
# RODEO_AWS_REGION, else this default, and the subnet is the region's default
# VPC public subnet. Not the AWS CLI's configured region on purpose: an account
# guardrail can leave that region without internet egress (seen on eu-west-1).
DEFAULT_REGION = "eu-north-1"
REGION_ENV = "RODEO_AWS_REGION"


def default_region() -> str:
    """Region for a plan that names none."""
    return os.environ.get(REGION_ENV, "").strip() or DEFAULT_REGION

# Mirrors the kvm_host firewalld rules a workshop host actually needs open:
# SSH, the Harvester UI DNAT (harvester_ui_port default), the Rancher NodePort
# DNAT (rancher_nodeport default). See rodeo/data/ansible/roles/kvm_host/
# defaults/main.yml and tasks/firewall.yml. If those ansible defaults ever
# change, this tuple should move with them.
MANAGED_SG_PORTS = (22, 8443, 30002)
# Source added to lab UI ports by `rodeo fleet open-access` (never port 22).
STUDENT_ACCESS_CIDR = "0.0.0.0/0"
# Claim portal VM (F5.2): its own SG. 80 is only for the ACME HTTP-01 challenge
# (Caddy redirects everything else to 443); 22 stays operator-only.
PORTAL_PUBLIC_PORTS = (80, 443)
PORTAL_SG_RESOURCE = "portal-security-group"
DEFAULT_PORTAL_INSTANCE_TYPE = "t3.small"

# Recommended default for performance-first Harvester labs (local NVMe).
DEFAULT_INSTANCE_TYPE = "i7i.8xlarge"

# SLES 16 pay-as-you-go, published by SUSE through Amazon (owner 013907871322,
# UsageOperation RunInstances:000g). Pin with provider.ami when you need a fixed
# image; otherwise the newest match wins.
#
# Why not openSUSE Leap, and why not BYOS — both were tried and both fail:
#
#   * Leap 16 is a Marketplace product whose listing does not permit 7th-gen
#     Intel instance types. Nested virtualisation via CpuOptions needs exactly
#     those (i7i / m7i), so Leap + i7i.8xlarge — the two former defaults — is
#     rejected with UnsupportedOperation, in every account and region. Leap's
#     only nested-virt-capable option is *.metal, at 60-80% more per hour for
#     less local NVMe.
#   * The SLES BYOS images allow i7i, but carry no subscription, so zypper has
#     no repos and install-deps fails. rodeo has no SUSEConnect step to fix
#     that, so BYOS cannot be a working default.
#
# PAYG resolves both: repos work with no registration, i7i is allowed, and the
# licence surcharge is ~$0.125/hr on i7i.8xlarge (~$1 per 8-hour workshop).
#
# The v???????? date anchor is deliberate: a looser "v*" also matches the -ecs-
# and -sapcal- variants, which are different images.
DEFAULT_AMI_NAME_FILTER = "suse-sles-16-0-v????????-hvm-ssd-x86_64"
DEFAULT_AMI_OWNERS = ("013907871322",)

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


def _tag(inst: dict[str, Any], key: str) -> str | None:
    for t in inst.get("Tags") or []:
        if t.get("Key") == key:
            return str(t.get("Value"))
    return None


def _tags(resource: dict[str, Any]) -> dict[str, str]:
    return {str(t.get("Key")): str(t.get("Value")) for t in resource.get("Tags") or []}


def _tag_list(tags: dict[str, str]) -> list[dict[str, str]]:
    return [{"Key": k, "Value": v} for k, v in tags.items()]


def _rodeo_version() -> str:
    from .. import __version__

    return str(__version__)


def _sts_caller_arn(region: str) -> str:
    """The caller ARN from ``sts get-caller-identity`` (the ``Arn`` field).

    The whole ARN, not the session name: a shared role whose session name is
    ``aws-cli`` would otherwise give every caller the same owner."""
    _require_boto3()
    import boto3

    try:
        return str(boto3.client("sts", region_name=region or None).get_caller_identity()["Arn"])
    except Exception as exc:  # noqa: BLE001 — botocore errors vary
        raise ConfigError(
            f"could not read the AWS caller identity (sts get-caller-identity): {exc}. "
            f"Log in to AWS, or set {OWNER_ENV} in CI."
        ) from exc


def caller_owner(region: str = "", sts_fn: Callable[[str], str] = _sts_caller_arn) -> str:
    """``rodeo-owner`` for this caller: ``RODEO_OWNER`` when set (STS is not
    called), else the caller ARN."""
    env = os.environ.get(OWNER_ENV, "").strip()
    if env:
        return check_owner(env)
    return check_owner(sts_fn(region))


def is_mine(inst: dict[str, Any], *, workshop: str, key: LabKey | None, owner: str) -> bool:
    """Whether ``inst`` belongs to this caller's deployment of ``workshop``.

    Tagged instances (issue #175) match on deployment id + owner. Instances from
    before that match only when this machine holds the lab key they were
    launched with. Without a local key nothing matches by tag: the lab name is
    shared across people and proves nothing."""
    tags = _tags(inst)
    if tags.get(TAG_MANAGED_BY) != TAG_MANAGED_BY_VALUE or key is None:
        return False
    dep = tags.get(TAG_DEPLOYMENT)
    if dep:
        return bool(key.deployment) and dep == key.deployment and tags.get(TAG_OWNER) == owner
    return tags.get(TAG_WORKSHOP) == workshop and inst.get("KeyName") == key.key_name


def _require_boto3():
    try:
        import boto3  # noqa: F401
        import botocore  # noqa: F401
    except ImportError as exc:
        import sys

        # rodeo-cli is not on PyPI: install the extra into the environment rodeo
        # itself runs from (works for install.sh, a dev venv or pipx alike).
        raise ConfigError(
            "AWS provider requires boto3 (the [aws] extra). Install it with: "
            f"{sys.executable} -m pip install 'boto3[crt]>=1.34'"
        ) from exc


def _detect_caller_ip() -> str:
    """This machine's public IP, to scope the auto-managed security group to
    "operator only" — the same convention already used by hand-created
    rodeo-workshop SGs. Uses AWS's own checkip endpoint so auto-management
    adds no new third-party trust beyond AWS itself, which the provider
    already requires."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=5) as resp:
            ip = resp.read().decode().strip()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise ConfigError(
            "could not auto-detect this machine's public IP to scope the "
            "auto-managed security group (needed because "
            "provider.security_group_ids is unset). Set "
            "provider.security_group_ids explicitly to skip auto-detection. "
            f"Underlying error: {exc}"
        ) from exc
    if not ip or ("." not in ip and ":" not in ip):
        raise ConfigError(
            f"unexpected response from IP-detection service: {ip!r} — set "
            "provider.security_group_ids explicitly to skip auto-detection"
        )
    return ip


class AwsHostProvider:
    """Provision/reuse/terminate EC2 instances tagged for a workshop."""

    name = "aws"

    def __init__(
        self,
        *,
        ec2_client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        caller_ip_fn: Callable[[], str] = _detect_caller_ip,
        sts_fn: Callable[[str], str] = _sts_caller_arn,
    ) -> None:
        self._ec2 = ec2_client
        self._sleep = sleep
        self._caller_ip_fn = caller_ip_fn
        self._sts_fn = sts_fn
        self._owner_cache: str | None = None

    def owner(self, config: dict[str, Any]) -> str:
        """This caller's ``rodeo-owner`` value (cached per provider)."""
        if self._owner_cache is None:
            self._owner_cache = caller_owner(str(config.get("region") or ""), self._sts_fn)
        return self._owner_cache

    def identity_tags(self, config: dict[str, Any], key: LabKey) -> dict[str, str]:
        if not key.deployment:
            raise ConfigError(
                f"lab key {key.key_name!r} has no deployment id; remove "
                f"{key.private.parent} and deploy again"
            )
        return deployment_tags(self.owner(config), key.deployment, _rodeo_version())

    def _client(self, config: dict[str, Any]) -> Any:
        if self._ec2 is not None:
            return self._ec2
        _require_boto3()
        import boto3

        return boto3.client("ec2", region_name=str(config["region"]))

    def default_subnet(self, config: dict[str, Any], *, instance_type: str = "") -> str:
        """The default VPC's public subnet for ``config['region']``.

        Picks a default-for-AZ subnet that auto-assigns public IPs, in an AZ
        that offers ``instance_type`` when one is given, first by AZ name so
        re-runs pick the same one. Fails with the fields to set when the region
        has no default VPC or no such subnet.
        """
        region = str(config["region"])
        ec2 = self._client(config)
        vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}]).get("Vpcs") or []
        if not vpcs:
            raise ConfigError(
                f"no default VPC in {region}: set provider.subnet_id (and provider.region) "
                "in rodeo-plan.yaml, or export RODEO_AWS_REGION to a region that has one"
            )
        subnets = ec2.describe_subnets(
            Filters=[
                {"Name": "vpc-id", "Values": [str(vpcs[0]["VpcId"])]},
                {"Name": "default-for-az", "Values": ["true"]},
            ]
        ).get("Subnets") or []
        public = [s for s in subnets if s.get("MapPublicIpOnLaunch")]
        if instance_type and public:
            offered = ec2.describe_instance_type_offerings(
                LocationType="availability-zone",
                Filters=[{"Name": "instance-type", "Values": [instance_type]}],
            ).get("InstanceTypeOfferings") or []
            zones = {str(o.get("Location")) for o in offered}
            public = [s for s in public if s.get("AvailabilityZone") in zones]
        if not public:
            what = f" in an AZ that offers {instance_type}" if instance_type else ""
            raise ConfigError(
                f"no public default subnet{what} in {region}: set provider.subnet_id "
                "in rodeo-plan.yaml"
            )
        public.sort(key=lambda s: (str(s.get("AvailabilityZone", "")), str(s["SubnetId"])))
        return str(public[0]["SubnetId"])

    def validate(self, config: dict[str, Any]) -> None:
        if str(config.get("type") or "").lower() != "aws":
            raise ConfigError("AwsHostProvider requires provider.type: aws")
        deadman.ttl_hours(config)  # fail before anything is created
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
        sgs = config.get("security_group_ids")
        if sgs is not None and (
            not isinstance(sgs, list) or not sgs or not all(isinstance(x, str) for x in sgs)
        ):
            raise ConfigError(
                "provider.security_group_ids, when set, must be a non-empty list of strings "
                "(omit it entirely to let rodeo create and manage one automatically)"
            )
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

    def assert_public_ingress(self, ec2: Any, config: dict[str, Any]) -> None:
        """Fail closed when the subnet cannot give the host a *reachable* public IP.

        A workshop host is only useful if students can SSH to it, and rodeo hands
        out public-IP endpoints via ``AssociatePublicIpAddress``. AWS will happily
        assign a public IP in a subnet whose route table has no internet gateway:
        the address is allocated and routes nowhere, so provision "succeeds" and
        the deploy then hangs waiting for SSH. The host also has no egress, so
        ``install-deps`` cannot reach the distro repos even from the console.

        RunInstances DryRun does not catch this — it validates the request, not
        reachability — so this is a separate probe of the subnet's effective
        route table.

        Skipped when ``provider.associate_public_ip`` is explicitly false: the
        operator has opted into a private topology (bastion / VPN / SSM) and is
        not relying on public reachability.
        """
        if not bool(config.get("associate_public_ip", True)):
            return
        subnet_id = str(config["subnet_id"])
        region = str(config["region"])

        # The subnet's explicitly associated route table, else the VPC's main one.
        tables = (
            ec2.describe_route_tables(
                Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
            ).get("RouteTables")
            or []
        )
        vpc_id = ""
        if not tables:
            subnets = ec2.describe_subnets(SubnetIds=[subnet_id]).get("Subnets") or []
            if not subnets:
                raise ConfigError(f"provider.subnet_id {subnet_id} not found in {region}")
            vpc_id = str(subnets[0].get("VpcId") or "")
            tables = (
                ec2.describe_route_tables(
                    Filters=[
                        {"Name": "vpc-id", "Values": [vpc_id]},
                        {"Name": "association.main", "Values": ["true"]},
                    ]
                ).get("RouteTables")
                or []
            )

        via_nat = False
        for table in tables:
            for route in table.get("Routes") or []:
                if route.get("DestinationCidrBlock") != "0.0.0.0/0":
                    continue
                if str(route.get("State") or "active") != "active":
                    continue
                if str(route.get("GatewayId") or "").startswith("igw-"):
                    return
                if route.get("NatGatewayId") or str(
                    route.get("GatewayId") or ""
                ).startswith("nat-"):
                    via_nat = True

        where = f"subnet {subnet_id}" + (f" (vpc {vpc_id})" if vpc_id else "")
        if via_nat:
            raise ConfigError(
                f"{where} in {region} routes 0.0.0.0/0 through a NAT gateway, which "
                "is egress-only — the host can reach the internet but students "
                "cannot SSH in to a public IP. Use a subnet whose route table has "
                "an internet gateway route, or set provider.associate_public_ip: "
                "false if you front the lab with a bastion."
            )
        raise ConfigError(
            f"{where} in {region} has no 0.0.0.0/0 route to an internet gateway, so "
            "a public IP assigned there is unreachable: students could not SSH in "
            "and the host would have no egress for install-deps. Pick a subnet in a "
            "VPC with an internet gateway attached (check: an igw- route in the "
            "subnet's route table), or set provider.associate_public_ip: false if "
            "the lab is reached over a bastion / VPN instead."
        )

    @staticmethod
    def _vpc_id_for_subnet(ec2: Any, subnet_id: str) -> str:
        subnets = ec2.describe_subnets(SubnetIds=[subnet_id]).get("Subnets") or []
        if not subnets:
            raise ConfigError(f"provider.subnet_id {subnet_id} not found")
        return str(subnets[0].get("VpcId") or "")

    @staticmethod
    def _default_sg_for_vpc(ec2: Any, vpc_id: str) -> str:
        """The VPC's built-in 'default' SG — used only to give a capacity
        DryRun something real to reference when no workshop-scoped managed SG
        exists yet. Never created, tagged, or cleaned up by rodeo."""
        resp = ec2.describe_security_groups(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "group-name", "Values": ["default"]},
            ]
        )
        groups = resp.get("SecurityGroups") or []
        if not groups:
            raise ConfigError(
                f"no default security group found in vpc {vpc_id} to probe "
                "capacity with — set provider.security_group_ids explicitly"
            )
        return str(groups[0]["GroupId"])

    @staticmethod
    def _find_managed_sg(
        ec2: Any, *, vpc_id: str, identity: dict[str, str], resource: str = LAB_SG_RESOURCE,
    ) -> str | None:
        """This deployment's rodeo-managed SG (lab or portal), by deployment +
        owner tags. Never by name: two people may run labs with the same name."""
        resp = ec2.describe_security_groups(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": f"tag:{TAG_MANAGED_BY}", "Values": [TAG_MANAGED_BY_VALUE]},
                {"Name": f"tag:{TAG_DEPLOYMENT}", "Values": [identity[TAG_DEPLOYMENT]]},
                {"Name": f"tag:{TAG_OWNER}", "Values": [identity[TAG_OWNER]]},
                {"Name": f"tag:{TAG_RESOURCE}", "Values": [resource]},
            ]
        )
        groups = resp.get("SecurityGroups") or []
        return str(groups[0]["GroupId"]) if groups else None

    @staticmethod
    def _create_sg(
        ec2: Any, *, vpc_id: str, name: str, description: str, tags: dict[str, str],
    ) -> str:
        """Create a tagged SG. On a duplicate name, reuse the existing group only
        when it carries this deployment and owner; never reuse by name alone."""
        try:
            resp = ec2.create_security_group(
                GroupName=name,
                # EC2's GroupDescription rejects non-ASCII (rejected an em
                # dash live on 2026-09-11), so keep this string plain ASCII.
                Description=description,
                VpcId=vpc_id,
                TagSpecifications=[{"ResourceType": "security-group", "Tags": _tag_list(tags)}],
            )
            return str(resp["GroupId"])
        except Exception as exc:  # noqa: BLE001
            code, message = _aws_error_parts(exc)
            if code != "InvalidGroup.Duplicate":
                raise ConfigError(
                    f"could not create security group {name!r} in vpc {vpc_id}: {message}"
                ) from exc
            resp = ec2.describe_security_groups(
                Filters=[
                    {"Name": "vpc-id", "Values": [vpc_id]},
                    {"Name": "group-name", "Values": [name]},
                ]
            )
            groups = resp.get("SecurityGroups") or []
            if not groups:
                raise ConfigError(
                    f"security group {name!r} reported as duplicate but not found "
                    f"in vpc {vpc_id}: transient AWS inconsistency, retry"
                ) from exc
            found = _tags(groups[0])
            if (found.get(TAG_DEPLOYMENT) != tags.get(TAG_DEPLOYMENT)
                    or found.get(TAG_OWNER) != tags.get(TAG_OWNER)):
                raise ConfigError(
                    f"security group {name!r} already exists in vpc {vpc_id} and belongs "
                    f"to another deployment or owner ({found.get(TAG_OWNER) or 'untagged'}). "
                    "rodeo will not reuse it; delete it or deploy again for a new id."
                ) from exc
            return str(groups[0]["GroupId"])

    @classmethod
    def _create_managed_sg(
        cls, ec2: Any, *, vpc_id: str, workshop: str, identity: dict[str, str],
    ) -> str:
        return cls._create_sg(
            ec2,
            vpc_id=vpc_id,
            name=f"rodeo-{safe_lab(workshop)[:200]}-{identity[TAG_DEPLOYMENT]}",
            description=(
                f"rodeo-managed: {workshop} - SSH + Harvester UI + Rancher "
                f"NodePort ({', '.join(str(p) for p in MANAGED_SG_PORTS)})"
            ),
            tags={
                TAG_MANAGED_BY: TAG_MANAGED_BY_VALUE,
                TAG_WORKSHOP: workshop,
                TAG_RESOURCE: LAB_SG_RESOURCE,
                **identity,
            },
        )

    def _reconcile_managed_sg_ingress(
        self,
        ec2: Any,
        sg_id: str,
        caller_cidr: str,
        open_ports: tuple[int, ...] = (),
    ) -> None:
        """Desired state: exactly one ingress source (the caller's current
        public IP) per managed port, plus ``0.0.0.0/0`` on ``open_ports``.
        Revokes any other CIDR on those ports — safe because this SG is
        exclusively rodeo's, nothing else touches it — and authorizes the
        missing ones. No-ops on repeat runs from the same IP.

        Only ``rodeo fleet open-access`` passes ``open_ports``; every other
        caller keeps the operator-only default, so a re-provision closes any
        port a previous open-access opened."""
        resp = ec2.describe_security_groups(GroupIds=[sg_id])
        perms = (resp.get("SecurityGroups") or [{}])[0].get("IpPermissions") or []
        # The student SSH port has no operator rule (the operator uses :22): it
        # exists only while open-access has it open, and is removed otherwise.
        for port in (*MANAGED_SG_PORTS, STUDENT_SSH_PORT):
            desired = set() if port == STUDENT_SSH_PORT else {caller_cidr}
            if port in open_ports:
                desired.add(STUDENT_ACCESS_CIDR)
            current = {
                str(r.get("CidrIp"))
                for perm in perms
                if perm.get("IpProtocol") == "tcp"
                and perm.get("FromPort") == port
                and perm.get("ToPort") == port
                for r in perm.get("IpRanges") or []
                if r.get("CidrIp")
            }
            if current == desired:
                continue
            stale = current - desired
            if stale:
                ec2.revoke_security_group_ingress(
                    GroupId=sg_id,
                    IpPermissions=[
                        {
                            "IpProtocol": "tcp",
                            "FromPort": port,
                            "ToPort": port,
                            "IpRanges": [{"CidrIp": c} for c in stale],
                        }
                    ],
                )
            missing = sorted(desired - current)
            if missing:
                ec2.authorize_security_group_ingress(
                    GroupId=sg_id,
                    IpPermissions=[
                        {
                            "IpProtocol": "tcp",
                            "FromPort": port,
                            "ToPort": port,
                            "IpRanges": [
                                {
                                    "CidrIp": c,
                                    "Description": (
                                        "rodeo operator IP"
                                        if c == caller_cidr
                                        else "rodeo student access"
                                    ),
                                }
                                for c in missing
                            ],
                        }
                    ],
                )

    def _resolve_security_groups(
        self,
        ec2: Any,
        config: dict[str, Any],
        *,
        workshop: str | None = None,
        identity: dict[str, str] | None = None,
    ) -> list[str]:
        """``provider.security_group_ids`` if given (BYO SG, unchanged
        behaviour); otherwise a rodeo-managed SG scoped to this machine's
        current public IP on :data:`MANAGED_SG_PORTS`.

        Without a ``workshop`` — the standalone pre-flight capacity check,
        before any workshop-scoped resource should exist yet — falls back to
        the VPC's default SG so the DryRun probe has something real to
        reference. The real managed SG is only created once provisioning is
        actually about to happen (see ``provision``), so a capacity failure
        never leaves one behind.
        """
        sgs = config.get("security_group_ids")
        if sgs:
            return list(sgs)
        vpc_id = self._vpc_id_for_subnet(ec2, str(config["subnet_id"]))
        if not workshop or identity is None:
            return [self._default_sg_for_vpc(ec2, vpc_id)]
        sg_id = self._find_managed_sg(ec2, vpc_id=vpc_id, identity=identity)
        if sg_id is None:
            sg_id = self._create_managed_sg(
                ec2, vpc_id=vpc_id, workshop=workshop, identity=identity
            )
        caller_ip = self._caller_ip_fn()
        self._reconcile_managed_sg_ingress(ec2, sg_id, f"{caller_ip}/32")
        if config.get("host_to_host"):
            self._ensure_host_to_host(ec2, sg_id)
        return [sg_id]

    @staticmethod
    def _ensure_host_to_host(ec2: Any, sg_id: str) -> None:
        """All traffic between the workshop's own hosts (``provider.host_to_host``),
        for labs whose hosts form a cluster. A self-referencing rule: only members
        of this SG match it, nothing from outside. The port reconcile above only
        touches CIDR rules, so it never removes this one."""
        resp = ec2.describe_security_groups(GroupIds=[sg_id])
        perms = (resp.get("SecurityGroups") or [{}])[0].get("IpPermissions") or []
        for perm in perms:
            if perm.get("IpProtocol") == "-1" and any(
                p.get("GroupId") == sg_id for p in perm.get("UserIdGroupPairs") or []
            ):
                return
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    "IpProtocol": "-1",
                    "UserIdGroupPairs": [
                        {"GroupId": sg_id, "Description": "rodeo host-to-host"}
                    ],
                }
            ],
        )

    def set_student_access(
        self,
        config: dict[str, Any],
        *,
        workshop: str,
        open_ports: tuple[int, ...],
        allow_ssh: bool = False,
    ) -> str:
        """Reconcile the workshop's managed SG to operator /32 plus
        ``0.0.0.0/0`` on ``open_ports`` (empty = close). Returns the SG id.

        Refuses BYO ``security_group_ids`` (not rodeo's to change) and a
        workshop without a managed SG yet (nothing provisioned)."""
        self.validate(config)
        if config.get("security_group_ids"):
            raise ConfigError(
                "provider.security_group_ids is set: rodeo does not change a "
                "security group it does not own. Open the lab ports on it yourself, "
                "or omit security_group_ids so rodeo manages one."
            )
        # :22 is never opened: the fleet-wide rodeo key logs in there as root
        # and ssh_user. Student SSH has its own port and sshd (student only).
        bad = sorted(
            p for p in open_ports
            if p == 22
            or (p == STUDENT_SSH_PORT and not allow_ssh)
            or p not in (*MANAGED_SG_PORTS, STUDENT_SSH_PORT)
        )
        if bad:
            raise ConfigError(f"ports {bad} cannot be opened to students")
        ec2 = self._client(config)
        vpc_id = self._vpc_id_for_subnet(ec2, str(config["subnet_id"]))
        key = load_lab_ssh_key(workshop)
        sg_id = (
            self._find_managed_sg(ec2, vpc_id=vpc_id, identity=self.identity_tags(config, key))
            if key is not None else None
        )
        if sg_id is None:
            raise ConfigError(
                f"no rodeo-managed security group of yours for workshop {workshop!r} "
                "(run `rodeo fleet provision` first, from the machine that deployed it)"
            )
        caller_ip = self._caller_ip_fn()
        self._reconcile_managed_sg_ingress(
            ec2, sg_id, f"{caller_ip}/32", open_ports=tuple(open_ports)
        )
        return sg_id

    def _cleanup_managed_sg(
        self,
        ec2: Any,
        config: dict[str, Any],
        *,
        identity: dict[str, str],
    ) -> DeprovisionResult | None:
        """Best-effort delete of this deployment's rodeo-managed lab SG.

        Only called when ``provider.security_group_ids`` was unset (rodeo
        owns the SG). SG deletion fails with DependencyViolation while any
        ENI is still attached, which is normal for a few dozen seconds after
        ``terminate_instances`` — retried with backoff, but never allowed to
        fail the destroy call: the SG costs nothing left behind, and a later
        `rodeo destroy` re-run or the tag-based sweep script will catch it.
        """
        try:
            subnet_id = str(config.get("subnet_id") or "")
            if not subnet_id:
                return None
            vpc_id = self._vpc_id_for_subnet(ec2, subnet_id)
            sg_id = self._find_managed_sg(ec2, vpc_id=vpc_id, identity=identity)
            if not sg_id:
                return None
            for attempt in range(6):
                try:
                    ec2.delete_security_group(GroupId=sg_id)
                    return DeprovisionResult(
                        id="security-group", ok=True, provider_id=sg_id, detail="deleted"
                    )
                except Exception as exc:  # noqa: BLE001
                    code, _ = _aws_error_parts(exc)
                    if code != "DependencyViolation":
                        return DeprovisionResult(
                            id="security-group",
                            ok=True,
                            provider_id=sg_id,
                            detail=f"cleanup skipped ({code or 'error'}) — safe to ignore",
                        )
                    if attempt < 5:
                        self._sleep(10)
            return DeprovisionResult(
                id="security-group",
                ok=True,
                provider_id=sg_id,
                detail="still attached (instance ENIs detaching) — re-run destroy "
                "in a minute, or it'll be caught by a tag-based sweep",
            )
        except Exception:  # noqa: BLE001 — cleanup must never fail the destroy call
            return None

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

        # Reachability before capacity: a host students can't SSH to is useless
        # even when the instance type is available.
        self.assert_public_ingress(ec2, config)

        # Capacity probe: DryRun with real networking + AMI.
        cfg = dict(config)
        cfg["ami"] = self.resolve_ami(ec2, cfg)
        # No workshop context here (standalone pre-flight): fall back to the
        # VPC's default SG rather than create the real managed one this early
        # — a capacity failure below must not leave a security group behind.
        cfg["security_group_ids"] = self._resolve_security_groups(ec2, cfg)
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
            # The key pair may not exist yet: provisioning imports it after this
            # check (ensure_ec2_key_pair), so a first deploy with a custom
            # provider.key_name failed here with InvalidKeyPair.NotFound. The
            # probe is about capacity; the key is checked before the real launch.
            kwargs.pop("KeyName", None)
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
        reject_reserved_labels(cfg.get("labels"))
        ec2 = self._client(cfg)
        cfg = dict(cfg)
        cfg["ami"] = self.resolve_ami(ec2, cfg)
        lab_key, mine = self._lab_key(ec2, cfg, spec.workshop)
        identity = self.identity_tags(cfg, lab_key)
        key_path = str(lab_key.private)
        # The lab's own key for the SSH wait, whatever ProvisionSpec carried.
        wait_spec = ProvisionSpec(
            workshop=spec.workshop,
            host_ids=spec.host_ids,
            ssh_user=spec.ssh_user,
            identity_file=key_path,
            extra_labels=spec.extra_labels,
            wait_ssh=spec.wait_ssh,
            ssh_timeout=spec.ssh_timeout,
        )
        cfg["key_name"] = lab_key.key_name
        cfg["public_key"] = lab_key.public.read_text()
        out: list[ProvisionedHost] = []
        to_create: list[str] = []
        existing_map: dict[str, dict[str, Any]] = {}
        for inst in mine:
            tags = _tags(inst)
            if tags.get(TAG_ROLE) == ROLE_PORTAL:
                continue
            hid = tags.get(TAG_HOST_ID)
            if hid and hid not in existing_map:
                existing_map[hid] = inst
        for host_id in wait_spec.host_ids:
            if host_id not in existing_map:
                to_create.append(host_id)
        if to_create:
            self.assert_available(cfg, count=len(to_create))
        # Real managed SG (create/reuse + reconcile to this machine's current
        # IP) only after capacity is confirmed: a failed capacity check must
        # not leave a security group behind. No-op when the operator supplied
        # provider.security_group_ids explicitly.
        cfg["security_group_ids"] = self._resolve_security_groups(
            ec2, cfg, workshop=wait_spec.workshop, identity=identity
        )
        for host_id in wait_spec.host_ids:
            if host_id in existing_map:
                inst = existing_map[host_id]
                action = "reuse"
                if inst["State"]["Name"] in ("stopped", "stopping"):
                    ec2.start_instances(InstanceIds=[inst["InstanceId"]])
            else:
                inst = self._create(ec2, wait_spec, cfg, host_id, identity)
                action = "create"
            inst = self._wait_running(ec2, inst["InstanceId"], timeout=float(cfg.get("wait_timeout") or 600))
            public_ip = (inst.get("PublicIpAddress") or "").strip()
            if action == "create":
                forget_host_key(wait_spec.workshop, public_ip)  # EC2 reuses public IPs
            if not public_ip:
                raise ConfigError(
                    f"AWS instance {inst['InstanceId']} ({host_id}) has no public IP: "
                    "enable associate_public_ip or use a subnet that assigns one"
                )
            labels = {
                "provider": "aws",
                **wait_spec.extra_labels,
                **(cfg.get("labels") or {}),
                "deployment": identity[TAG_DEPLOYMENT],
            }
            expiry_tag = _tag(inst, deadman.TAG_EXPIRES_AT)
            if expiry_tag:
                labels["expires_at"] = expiry_tag
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
                        "identity_file": key_path,
                    },
                    hosts=[],
                )
                fh = FleetHost(id=host.id, ssh=host.ssh, public_ip=host.public_ip)
                plant_rodeo_ssh_key(inv, fh, key=lab_key)
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
        """Terminate this caller's deployment of ``spec.workshop`` (never a
        teammate's lab with the same name): matched by deployment id + owner,
        by the local key for labs from before issue #175, or, without a local
        key, only the instance ids this machine recorded."""
        self.validate(config)
        ec2 = self._client(config)
        owner = self.owner(config)
        key = load_lab_ssh_key(spec.workshop)
        if key is not None:
            lab = self._mine(ec2, spec.workshop, key)
            self._adopt(ec2, config, spec.workshop, key, lab)
            identity: dict[str, str] | None = self.identity_tags(config, key)
        else:
            lab = self._recorded(ec2, spec, owner)
            deps = {_tags(i).get(TAG_DEPLOYMENT) for i in lab} - {None}
            identity = (
                deployment_tags(owner, deps.pop(), _rodeo_version()) if len(deps) == 1 else None
            )
        targets: list[tuple[str, str]] = []  # host_id, instance_id
        want = set(spec.host_ids) if spec.host_ids else None
        for inst in lab:
            tags = _tags(inst)
            hid = tags.get(TAG_HOST_ID) or inst["InstanceId"]
            if tags.get(TAG_ROLE) == ROLE_PORTAL:
                continue  # only deprovision_portal terminates the portal
            if want is not None and hid not in want:
                continue
            targets.append((hid, inst["InstanceId"]))

        if not targets:
            results = [
                DeprovisionResult(id=hid, ok=True, detail="no matching instance")
                for hid in (spec.host_ids or [])
            ] or [
                DeprovisionResult(id="*", ok=True, detail="no matching instances")
            ]
        else:
            ids = [iid for _, iid in targets]
            try:
                ec2.terminate_instances(InstanceIds=ids)
                results = [
                    DeprovisionResult(id=hid, ok=True, provider_id=iid, detail="terminating")
                    for hid, iid in targets
                ]
            except Exception as exc:  # noqa: BLE001
                results = [
                    DeprovisionResult(id=hid, ok=False, error=str(exc), provider_id=iid)
                    for hid, iid in targets
                ]

        # Managed-SG cleanup (only ours to delete when the operator didn't
        # supply provider.security_group_ids), and only once nothing else of
        # this deployment is still alive: `spec.host_ids` can scope a partial
        # teardown (Fleet destroying one student while others keep running).
        # A fresh re-query is the authoritative check, correct whether
        # termination above succeeded, failed or found nothing to do.
        terminated_ids = {iid for _, iid in targets}
        if not config.get("security_group_ids") and identity is not None:
            remaining = self._deployment_instances(ec2, identity)
            if key is not None:
                remaining += self._mine(ec2, spec.workshop, key)
            # The portal VM (F5.2) has its own SG and lifecycle: it must not keep
            # the lab SG alive, or `fleet deprovision --keep-portal` leaks it.
            still_alive = any(
                inst["InstanceId"] not in terminated_ids
                and _tag(inst, TAG_ROLE) != ROLE_PORTAL
                for inst in remaining
            )
            if not still_alive:
                cleanup = self._cleanup_managed_sg(ec2, config, identity=identity)
                if cleanup:
                    results.append(cleanup)
        # The lab key lives exactly as long as the lab: gone with its last
        # instance. The portal counts here (it was launched with this key).
        if key is not None and not self._mine(ec2, spec.workshop, key, exclude=terminated_ids):
            key_result = self._delete_lab_key(ec2, spec.workshop)
            if key_result:
                results.append(key_result)
        return results

    def _live_instances(self, ec2: Any, filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        resp = ec2.describe_instances(Filters=[
            *filters,
            {"Name": "instance-state-name", "Values": LIVE_STATES},
        ])
        return [
            inst
            for res in resp.get("Reservations") or []
            for inst in res.get("Instances") or []
        ]

    def _deployment_instances(self, ec2: Any, identity: dict[str, str]) -> list[dict[str, Any]]:
        """Live instances tagged with this deployment id and owner."""
        return self._live_instances(ec2, [
            {"Name": f"tag:{TAG_MANAGED_BY}", "Values": [TAG_MANAGED_BY_VALUE]},
            {"Name": f"tag:{TAG_DEPLOYMENT}", "Values": [identity[TAG_DEPLOYMENT]]},
            {"Name": f"tag:{TAG_OWNER}", "Values": [identity[TAG_OWNER]]},
        ])

    def _mine(
        self, ec2: Any, workshop: str, key: LabKey | None, *, exclude: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Live instances of this caller's deployment of ``workshop``, portal
        included: tagged with the key's deployment id + this owner, plus
        untagged ones from before issue #175 launched with this key."""
        if key is None:
            return []
        owner = self._owner_cache or ""
        found: dict[str, dict[str, Any]] = {}
        if key.deployment:
            for inst in self._live_instances(ec2, [
                {"Name": f"tag:{TAG_MANAGED_BY}", "Values": [TAG_MANAGED_BY_VALUE]},
                {"Name": f"tag:{TAG_DEPLOYMENT}", "Values": [key.deployment]},
            ]):
                found[inst["InstanceId"]] = inst
        for inst in self._live_instances(ec2, [
            {"Name": f"tag:{TAG_MANAGED_BY}", "Values": [TAG_MANAGED_BY_VALUE]},
            {"Name": f"tag:{TAG_WORKSHOP}", "Values": [workshop]},
        ]):
            found.setdefault(inst["InstanceId"], inst)
        skip = exclude or set()
        return [
            inst for iid, inst in found.items()
            if iid not in skip and is_mine(inst, workshop=workshop, key=key, owner=owner)
        ]

    def _recorded(self, ec2: Any, spec: ProvisionSpec, owner: str) -> list[dict[str, Any]]:
        """Destroy without the lab key: only instance ids this machine recorded,
        and only those still tagged for this lab and not owned by someone else."""
        ids = [i for i in spec.known_instance_ids if i]
        if not ids:
            raise ConfigError(
                f"lab {spec.workshop!r} has no SSH key on this machine and no recorded "
                "instance ids, so rodeo cannot tell which instances are yours. List "
                "your deployments with `rodeo cloud ls` and remove one with "
                "`rodeo cloud destroy <deployment>`."
            )
        out = []
        for inst in self._live_instances(ec2, [{"Name": "instance-id", "Values": ids}]):
            tags = _tags(inst)
            if tags.get(TAG_MANAGED_BY) != TAG_MANAGED_BY_VALUE:
                continue
            if tags.get(TAG_WORKSHOP) != spec.workshop:
                continue
            if tags.get(TAG_OWNER) not in (None, owner):
                continue
            out.append(inst)
        return out

    def _adopt(
        self,
        ec2: Any,
        config: dict[str, Any],
        workshop: str,
        key: LabKey,
        lab: list[dict[str, Any]],
    ) -> None:
        """Add the deployment tags to this caller's instances from before issue
        #175 (matched by the local key), plus their volumes, network interfaces,
        rodeo security groups and the key pair. Old resource names stay."""
        legacy = [i for i in lab if not _tag(i, TAG_DEPLOYMENT)]
        if not legacy:
            return
        identity = self.identity_tags(config, key)
        tag_list = _tag_list(identity)
        ec2.create_tags(Resources=[i["InstanceId"] for i in legacy], Tags=tag_list)
        for inst in legacy:
            inst["Tags"] = [*(inst.get("Tags") or []), *tag_list]
        extra: list[str] = []
        sg_ids: set[str] = set()
        for inst in legacy:
            for bdm in inst.get("BlockDeviceMappings") or []:
                vid = (bdm.get("Ebs") or {}).get("VolumeId")
                if vid:
                    extra.append(str(vid))
            for eni in inst.get("NetworkInterfaces") or []:
                if eni.get("NetworkInterfaceId"):
                    extra.append(str(eni["NetworkInterfaceId"]))
            sg_ids.update(str(g["GroupId"]) for g in inst.get("SecurityGroups") or [] if g.get("GroupId"))
        try:
            if sg_ids:
                groups = ec2.describe_security_groups(GroupIds=sorted(sg_ids)).get("SecurityGroups") or []
                for g in groups:
                    gt = _tags(g)
                    if (gt.get(TAG_MANAGED_BY) == TAG_MANAGED_BY_VALUE
                            and gt.get(TAG_WORKSHOP) == workshop
                            and not gt.get(TAG_DEPLOYMENT)):
                        extra.append(str(g["GroupId"]))
            pairs = ec2.describe_key_pairs(KeyNames=[key.key_name]).get("KeyPairs") or []
            extra.extend(str(p["KeyPairId"]) for p in pairs if p.get("KeyPairId"))
            if extra:
                ec2.create_tags(Resources=extra, Tags=tag_list)
        except Exception:  # noqa: BLE001 — the instances carry the tags; the rest is best-effort
            pass

    def _lab_key(
        self, ec2: Any, cfg: dict[str, Any], workshop: str,
    ) -> tuple[LabKey, list[dict[str, Any]]]:
        """The SSH key this deployment of ``workshop`` uses, and its live instances.

        A lab with live instances of this caller keeps the key it was created
        with (older untagged ones get adopted). Otherwise this is a new
        deployment: a freshly generated key and deployment id, after the
        previous key is deleted locally and in EC2. A teammate's lab with the
        same name is not ours and does not block this."""
        region = str(cfg.get("region") or "")
        self.owner(cfg)
        self._sweep_stale_lab_keys(ec2, region, skip=workshop)
        key = load_lab_ssh_key(workshop)
        mine = self._mine(ec2, workshop, key)
        if mine and key is not None:
            self._adopt(ec2, cfg, workshop, key, mine)
        else:
            if key is not None:
                delete_ec2_key_pair(ec2, key.key_name)
            key = new_lab_ssh_key(workshop, region=region)
            mine = []
        ensure_ec2_key_pair(
            ec2,
            key_name=key.key_name,
            public_key=key.public,
            tags={TAG_MANAGED_BY: TAG_MANAGED_BY_VALUE, TAG_WORKSHOP: workshop,
                  **self.identity_tags(cfg, key)},
        )
        return key, mine

    def _delete_lab_key(self, ec2: Any, workshop: str) -> DeprovisionResult | None:
        key = load_lab_ssh_key(workshop)
        if key is None:
            return None
        if not delete_ec2_key_pair(ec2, key.key_name):
            return DeprovisionResult(
                id="key-pair", ok=True, provider_id=key.key_name,
                detail="delete failed - re-run destroy to retry",
            )
        delete_lab_ssh_key(workshop)
        return DeprovisionResult(id="key-pair", ok=True, provider_id=key.key_name,
                                 detail="deleted")

    def _sweep_stale_lab_keys(self, ec2: Any, region: str, *, skip: str) -> None:
        """Delete this machine's lab keys whose lab is gone (dead-man switch,
        console termination): the key pair in EC2 and the private key here.
        Only keys recorded for ``region``, and only when no instance of that
        key's own deployment is left; never fails the caller."""
        try:
            for key in list_lab_ssh_keys():
                if key.workshop == skip or not key.region or key.region != region:
                    continue
                if self._mine(ec2, key.workshop, key):
                    continue
                if delete_ec2_key_pair(ec2, key.key_name):
                    delete_lab_ssh_key(key.workshop)
        except Exception:  # noqa: BLE001 — housekeeping only
            pass

    def _create(
        self,
        ec2: Any,
        spec: ProvisionSpec,
        config: dict[str, Any],
        host_id: str,
        identity: dict[str, str],
    ) -> dict[str, Any]:
        kwargs = self._run_instances_kwargs(
            config,
            host_id=host_id,
            workshop=spec.workshop,
            ssh_user=spec.ssh_user,
            extra_labels=spec.extra_labels,
            identity=identity,
            dry_run=False,
            min_count=1,
            max_count=1,
            lab_host=True,
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
        identity: dict[str, str] | None = None,
        dry_run: bool = False,
        min_count: int = 1,
        max_count: int = 1,
        lab_host: bool = False,
    ) -> dict[str, Any]:
        reject_reserved_labels(config.get("labels"))
        tags = {"Name": f"{workshop}-{host_id}"}
        for k, v in (config.get("labels") or {}).items():
            tags[str(k)] = str(v)
        for k, v in (extra_labels or {}).items():
            tags[str(k)] = str(v)
        # Identity, ownership and expiry go last so no label can replace them:
        # deprovision finds instances by these tags, and a lost one would leak
        # the instance or let someone else's destroy match it.
        tags.update(identity or {})
        tags.update(ownership_tags(workshop, host_id))
        # Dead-man switch (deadman.py): every instance rodeo creates expires,
        # except kvm-hosts labs that turned it off (tagged "never").
        expiry = deadman.expires_at(config)
        tags[deadman.TAG_EXPIRES_AT] = deadman.iso(expiry) if expiry else deadman.NEVER

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
            # Instance first (tests read index 0). AWS does not copy instance
            # tags onto the root volume or the ENI, so both get their own entry
            # and the disk shows up under the same tags on the bill (#175).
            "TagSpecifications": [
                {"ResourceType": rtype, "Tags": _tag_list(tags)}
                for rtype in ("instance", "volume", "network-interface")
            ],
            "DryRun": dry_run,
            # The guest's dead-man timer powers it off; AWS then terminates it
            # instead of keeping a stopped instance that still bills its disks.
            # Without a switch, a poweroff from inside only stops the host, so a
            # week-long lab survives it; `rodeo fleet destroy` terminates it.
            "InstanceInitiatedShutdownBehavior": "terminate" if expiry else "stop",
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
            # Lab hosts start installing the KVM stack at boot, while the
            # laptop still waits for SSH. Not the portal VM: it runs no labs.
            boot_script = None
            if lab_host:
                from ..commands.install_deps import zypper_boot_install_script

                boot_script = zypper_boot_install_script()
            kwargs["UserData"] = build_ec2_userdata(
                ssh_user=ssh_user, expires_at=expiry, boot_script=boot_script,
                public_key=config.get("public_key"),
            )
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
            result = run_remote(inventory, fh, ["true"], timeout=20.0, as_root=False)
            if result.ok and need_sudo:
                # cloud-init may finish SSH before sudoers drop-in is in place
                sudo_ok = run_remote(
                    inventory, fh, ["sudo", "-n", "true"], timeout=20.0, as_root=False
                )
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

    # ------------------------------------------------------------------ portal VM (F5.2)
    def _ensure_portal_sg(
        self, ec2: Any, *, vpc_id: str, workshop: str, identity: dict[str, str],
    ) -> str:
        """Own SG for the portal: 80/443 from anywhere, 22 from the operator /32.

        Tagged ``rodeo-resource: portal-security-group`` so the lab SG lookup
        (filters on ``security-group``) can never return it."""
        sg_id = self._find_managed_sg(
            ec2, vpc_id=vpc_id, identity=identity, resource=PORTAL_SG_RESOURCE
        )
        if sg_id is None:
            sg_id = self._create_sg(
                ec2,
                vpc_id=vpc_id,
                name=f"rodeo-{safe_lab(workshop)[:200]}-{identity[TAG_DEPLOYMENT]}-portal",
                description=f"rodeo-managed: {workshop} claim portal - HTTPS 443, ACME 80, SSH operator",
                tags={
                    TAG_MANAGED_BY: TAG_MANAGED_BY_VALUE,
                    TAG_WORKSHOP: workshop,
                    TAG_RESOURCE: PORTAL_SG_RESOURCE,
                    **identity,
                },
            )
        caller = f"{self._caller_ip_fn()}/32"
        desired = {22: {caller}, **{p: {STUDENT_ACCESS_CIDR} for p in PORTAL_PUBLIC_PORTS}}
        perms = (ec2.describe_security_groups(GroupIds=[sg_id]).get("SecurityGroups") or [{}])[0]
        current: dict[int, set[str]] = {}
        for perm in perms.get("IpPermissions") or []:
            if perm.get("IpProtocol") == "tcp" and perm.get("FromPort") == perm.get("ToPort"):
                current.setdefault(int(perm["FromPort"]), set()).update(
                    str(r["CidrIp"]) for r in perm.get("IpRanges") or [] if r.get("CidrIp")
                )
        for port in sorted(set(desired) | set(current)):
            want, have = desired.get(port, set()), current.get(port, set())
            if have - want:
                ec2.revoke_security_group_ingress(GroupId=sg_id, IpPermissions=[{
                    "IpProtocol": "tcp", "FromPort": port, "ToPort": port,
                    "IpRanges": [{"CidrIp": c} for c in sorted(have - want)]}])
            if want - have:
                ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[{
                    "IpProtocol": "tcp", "FromPort": port, "ToPort": port,
                    "IpRanges": [{"CidrIp": c, "Description": "rodeo portal"}
                                 for c in sorted(want - have)]}])
        return sg_id

    @staticmethod
    def _portal_of(lab: list[dict[str, Any]]) -> dict[str, Any] | None:
        for inst in lab:
            if _tag(inst, TAG_ROLE) == ROLE_PORTAL:
                return inst
        return None

    def provision_portal(
        self,
        config: dict[str, Any],
        *,
        workshop: str,
        ssh_user: str,
        instance_type: str | None = None,
        wait_ssh: bool = True,
        ssh_timeout: float = 600.0,
    ) -> ProvisionedHost:
        """Create or reuse the workshop's claim portal VM.

        Unlike lab hosts: small instance, no nested virtualisation, its own SG, and
        **no** ``plant_rodeo_ssh_key`` - only the public key reaches the portal (via
        cloud-init), so a compromised portal cannot reach any lab host."""
        self.validate(config)
        ec2 = self._client(config)
        cfg = dict(config)
        cfg["ami"] = self.resolve_ami(ec2, cfg)
        lab_key, mine = self._lab_key(ec2, cfg, workshop)
        tags_identity = self.identity_tags(cfg, lab_key)
        identity = str(lab_key.private)
        vpc_id = self._vpc_id_for_subnet(ec2, str(cfg["subnet_id"]))
        sg_id = self._ensure_portal_sg(ec2, vpc_id=vpc_id, workshop=workshop,
                                       identity=tags_identity)
        inst = self._portal_of(mine)
        action = "reuse"
        if inst is None:
            portal_cfg = {
                **cfg,
                "key_name": lab_key.key_name,
                "public_key": lab_key.public.read_text(),
                "instance_type": instance_type or DEFAULT_PORTAL_INSTANCE_TYPE,
                "nested_virtualization": False,
                "volume_size_gib": int(cfg.get("portal_volume_size_gib") or 20),
                "security_group_ids": [sg_id],
                "labels": {},
            }
            kwargs = self._run_instances_kwargs(
                portal_cfg, host_id=ROLE_PORTAL, workshop=workshop, ssh_user=ssh_user,
                extra_labels={TAG_ROLE: ROLE_PORTAL},
                identity=tags_identity,
            )
            inst = ec2.run_instances(**kwargs)["Instances"][0]
            action = "create"
        elif inst["State"]["Name"] in ("stopped", "stopping"):
            ec2.start_instances(InstanceIds=[inst["InstanceId"]])
        inst = self._wait_running(ec2, inst["InstanceId"], timeout=float(cfg.get("wait_timeout") or 600))
        ip = str(inst.get("PublicIpAddress") or "")
        if action == "create":
            forget_host_key(workshop, ip)
        portal_labels = {"provider": "aws", "provision_action": action}
        expiry_tag = _tag(inst, deadman.TAG_EXPIRES_AT)
        if expiry_tag:
            portal_labels["expires_at"] = expiry_tag
        host = ProvisionedHost(id=ROLE_PORTAL, ssh=ip, public_ip=ip,
                               labels=portal_labels,
                               provider_id=inst["InstanceId"])
        if wait_ssh:
            spec = ProvisionSpec(workshop=workshop, host_ids=[ROLE_PORTAL], ssh_user=ssh_user,
                                 identity_file=identity)
            self._wait_ssh(spec, host, timeout=ssh_timeout)
        return host

    def deprovision_portal(self, config: dict[str, Any], *, workshop: str) -> list[DeprovisionResult]:
        """Terminate the portal VM, then delete its SG once its ENI is gone."""
        self.validate(config)
        ec2 = self._client(config)
        self.owner(config)
        key = load_lab_ssh_key(workshop)
        if key is None:
            raise ConfigError(
                f"lab {workshop!r} has no SSH key on this machine, so rodeo cannot tell "
                "which portal is yours. Use `rodeo cloud ls` and `rodeo cloud destroy "
                "<deployment>`."
            )
        lab = self._mine(ec2, workshop, key)
        self._adopt(ec2, config, workshop, key, lab)
        tags_identity = self.identity_tags(config, key)
        results: list[DeprovisionResult] = []
        inst = self._portal_of(lab)
        if inst is not None:
            ec2.terminate_instances(InstanceIds=[inst["InstanceId"]])
            results.append(DeprovisionResult(id=ROLE_PORTAL, ok=True,
                                             provider_id=inst["InstanceId"], detail="terminating"))
        else:
            results.append(DeprovisionResult(id=ROLE_PORTAL, ok=True, detail="no portal instance"))
        try:
            vpc_id = self._vpc_id_for_subnet(ec2, str(config["subnet_id"]))
            sg_id = self._find_managed_sg(
                ec2, vpc_id=vpc_id, identity=tags_identity, resource=PORTAL_SG_RESOURCE
            )
        except Exception:  # noqa: BLE001 - cleanup never fails the teardown
            sg_id = None
        if sg_id:
            detail = "still attached - re-run deprovision in a minute"
            for attempt in range(12):
                try:
                    ec2.delete_security_group(GroupId=sg_id)
                    detail = "deleted"
                    break
                except Exception as exc:  # noqa: BLE001
                    code, _ = _aws_error_parts(exc)
                    if code != "DependencyViolation":
                        detail = f"cleanup skipped ({code or 'error'})"
                        break
                    if attempt < 11:
                        self._sleep(10)
            results.append(DeprovisionResult(id="portal-security-group", ok=True,
                                             provider_id=sg_id, detail=detail))
        exclude = {inst["InstanceId"]} if inst is not None else set()
        if not self._mine(ec2, workshop, key, exclude=exclude):
            key_result = self._delete_lab_key(ec2, workshop)
            if key_result:
                results.append(key_result)
        return results

    # ------------------------------------------------------------------ rodeo cloud (issue #175)
    def list_deployments(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        """Every live rodeo instance in the region, grouped by deployment.

        Instances from before issue #175 have no deployment id: they are grouped
        by lab name + key pair and flagged ``legacy``."""
        ec2 = self._client(config)
        groups: dict[str, dict[str, Any]] = {}
        for inst in self._live_instances(ec2, [
            {"Name": f"tag:{TAG_MANAGED_BY}", "Values": [TAG_MANAGED_BY_VALUE]},
        ]):
            tags = _tags(inst)
            dep = tags.get(TAG_DEPLOYMENT) or ""
            gid = dep or f"legacy:{tags.get(TAG_WORKSHOP, '')}:{inst.get('KeyName') or ''}"
            row = groups.setdefault(gid, {
                "deployment": dep,
                "legacy": not dep,
                "workshop": tags.get(TAG_WORKSHOP, ""),
                "owner": tags.get(TAG_OWNER, ""),
                "key_name": str(inst.get("KeyName") or ""),
                "hosts": [],
                "instances": [],
                "states": [],
                "launched": "",
                "expires": "",
            })
            row["hosts"].append(tags.get(TAG_HOST_ID) or inst["InstanceId"])
            row["instances"].append(inst["InstanceId"])
            row["states"].append(str((inst.get("State") or {}).get("Name") or ""))
            launched = inst.get("LaunchTime") or ""
            if launched and (not row["launched"] or launched < row["launched"]):
                row["launched"] = launched
            expires = tags.get(deadman.TAG_EXPIRES_AT, "")
            if expires and (not row["expires"] or expires > row["expires"]):
                row["expires"] = expires
        return sorted(groups.values(), key=lambda r: (r["workshop"], r["deployment"]))

    def destroy_deployment(
        self, config: dict[str, Any], deployment: str, *, allow_foreign: bool = False,
    ) -> list[DeprovisionResult]:
        """Terminate every instance of one deployment, then its security groups
        and key pair. Refuses a deployment owned by someone else unless
        ``allow_foreign`` (the CLI asks for the owner to be typed first)."""
        ec2 = self._client(config)
        owner = self.owner(config)
        insts = self._live_instances(ec2, [
            {"Name": f"tag:{TAG_MANAGED_BY}", "Values": [TAG_MANAGED_BY_VALUE]},
            {"Name": f"tag:{TAG_DEPLOYMENT}", "Values": [deployment]},
        ])
        dep_filter = [
            {"Name": f"tag:{TAG_MANAGED_BY}", "Values": [TAG_MANAGED_BY_VALUE]},
            {"Name": f"tag:{TAG_DEPLOYMENT}", "Values": [deployment]},
        ]
        sgs = ec2.describe_security_groups(Filters=dep_filter).get("SecurityGroups") or []
        try:
            pairs = ec2.describe_key_pairs(Filters=dep_filter).get("KeyPairs") or []
        except Exception:  # noqa: BLE001 — a missing pair is not an error here
            pairs = []
        owners = {_tags(r).get(TAG_OWNER, "") for r in [*insts, *sgs, *pairs]}
        if not owners:
            return [DeprovisionResult(id=deployment, ok=True, detail="nothing found")]
        foreign = sorted(o for o in owners if o != owner)
        if foreign and not allow_foreign:
            raise ConfigError(
                f"deployment {deployment} belongs to {', '.join(foreign)}, not to you "
                f"({owner}). Pass --not-mine to remove it anyway."
            )
        results: list[DeprovisionResult] = []
        if insts:
            ec2.terminate_instances(InstanceIds=[i["InstanceId"] for i in insts])
            results += [
                DeprovisionResult(id=_tag(i, TAG_HOST_ID) or i["InstanceId"], ok=True,
                                  provider_id=i["InstanceId"], detail="terminating")
                for i in insts
            ]
        for sg in sgs:
            results.append(self._delete_sg_retrying(ec2, str(sg["GroupId"])))
        for pair in pairs:
            name = str(pair.get("KeyName") or "")
            ok = delete_ec2_key_pair(ec2, name)
            results.append(DeprovisionResult(
                id="key-pair", ok=True, provider_id=name,
                detail="deleted" if ok else "delete failed - re-run to retry",
            ))
        return results

    def _delete_sg_retrying(self, ec2: Any, sg_id: str, attempts: int = 12) -> DeprovisionResult:
        detail = "still attached - re-run in a minute"
        for attempt in range(attempts):
            try:
                ec2.delete_security_group(GroupId=sg_id)
                detail = "deleted"
                break
            except Exception as exc:  # noqa: BLE001
                code, _ = _aws_error_parts(exc)
                if code != "DependencyViolation":
                    detail = f"cleanup skipped ({code or 'error'})"
                    break
                if attempt < attempts - 1:
                    self._sleep(10)
        return DeprovisionResult(id="security-group", ok=True, provider_id=sg_id, detail=detail)
