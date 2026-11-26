"""`rodeo fleet dns`: the player's names in the operator's Route 53 zone.

Laptop side only. Lab hosts and the portal never get AWS credentials.

Per lab, A records to the lab's public address: the player plus one name
per product UI in the lab profile's player.yaml (virt-rodeo shown)::

    <id>.<zone>          the player
    hv-<id>.<zone>       Harvester
    rancher-<id>.<zone>  Rancher

``<id>`` is the random ``labels.player_id`` (see inventory.ensure_player_ids).
Route 53 cannot tag a single record, so every name also gets a TXT record of
the same name, ``"managed-by=rodeo fleet=<workshop> host=<host id>"``. That
TXT is the ownership mark: publish refuses a name that holds anything not
marked for this lab, and delete only removes names marked for this workshop.
Nothing outside the zone named by ``player.domain`` is ever written.

A lab the dead-man switch terminated never runs ``fleet destroy``, so its
names would stay, pointing at an AWS address someone else may get next.
``prune`` removes every rodeo-marked name (any workshop) whose address is not
a live rodeo instance in the mark's region; ``fleet dns``, ``fleet up`` and
``fleet destroy`` run it, and ``fleet dns --prune`` runs it alone.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config import ConfigError
from .inventory import FleetHost, FleetInventory, player_id

MARK = "managed-by=rodeo"
TTL = 60
_BATCH = 400  # Route 53 allows 1000 changes per call; stay well below


@dataclass(frozen=True)
class DnsRow:
    id: str
    ok: bool
    names: tuple[str, ...] = ()
    detail: str = ""


def lab_names(domain: str, pid: str, keys: tuple[str, ...] = ("hv", "rancher")) -> dict[str, str]:
    """Same names as ``rodeo player setup --domain`` (rodeo.player.setup.SetupOptions.names)."""
    return {"player": f"{pid}.{domain}", **{k: f"{k}-{pid}.{domain}" for k in keys}}


def ui_keys(inventory: FleetInventory) -> tuple[str, ...]:
    """The UI keys of this workshop's lab profile (its player.yaml)."""
    from ..player.profile import load

    return tuple(u.key for u in load(inventory.lab_profile).uis)


def _txt_value(fleet: str, host_id: str, region: str = "") -> str:
    where = f" region={region}" if region else ""
    return f'"{MARK} fleet={fleet} host={host_id}{where}"'


def _parse_mark(value: str) -> dict[str, str] | None:
    words = value.strip('"').split()
    if not words or words[0] != MARK:
        return None
    return dict(w.split("=", 1) for w in words[1:] if "=" in w)


def _fqdn(name: str) -> str:
    return name.rstrip(".").lower() + "."


def route53_client() -> Any:
    from ..providers.aws import _require_boto3

    _require_boto3()
    import boto3

    return boto3.client("route53")


def find_zone(client: Any, domain: str) -> str:
    """Hosted zone id for exactly ``domain`` (public only)."""
    resp = client.list_hosted_zones_by_name(DNSName=domain, MaxItems="2")
    for zone in resp.get("HostedZones", []):
        if zone["Name"].rstrip(".").lower() == domain and not zone.get("Config", {}).get("PrivateZone"):
            return str(zone["Id"]).split("/")[-1]
    raise ConfigError(f"no public Route 53 hosted zone named {domain} in this AWS account")


def _all_records(client: Any, zone_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in client.get_paginator("list_resource_record_sets").paginate(HostedZoneId=zone_id):
        out.extend(page["ResourceRecordSets"])
    return out


def _apply(client: Any, zone_id: str, changes: list[dict[str, Any]], comment: str) -> None:
    for i in range(0, len(changes), _BATCH):
        resp = client.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={"Comment": comment[:256], "Changes": changes[i:i + _BATCH]},
        )
        client.get_waiter("resource_record_sets_changed").wait(
            Id=resp["ChangeInfo"]["Id"], WaiterConfig={"Delay": 5, "MaxAttempts": 60})


def _region(inventory: FleetInventory) -> str:
    return str((inventory.provider or {}).get("region") or "")


def _own_mark(rr: dict[str, Any]) -> dict[str, str] | None:
    """The mark of a TXT that is nothing but one rodeo mark, else None."""
    values = rr.get("ResourceRecords", [])
    if rr["Type"] != "TXT" or len(values) != 1:
        return None
    return _parse_mark(values[0]["Value"])


def _require_player(inventory: FleetInventory) -> str:
    if inventory.player is None:
        raise ConfigError("this workshop has no player: block (player.domain: <your zone>)")
    return inventory.player.domain


def plan_publish(inventory: FleetInventory, records: list[dict[str, Any]],
                 hosts: list[FleetHost]) -> tuple[list[dict[str, Any]], list[DnsRow]]:
    """UPSERTs for ``hosts`` and one row per host. Pure: no AWS calls."""
    domain = _require_player(inventory)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for rr in records:
        by_name.setdefault(_fqdn(rr["Name"]), []).append(rr)
    changes: list[dict[str, Any]] = []
    rows: list[DnsRow] = []
    keys = ui_keys(inventory)
    for host in hosts:
        pid = player_id(host)
        if not pid:
            rows.append(DnsRow(host.id, False, detail="no labels.player_id (run `rodeo fleet dns` again)"))
            continue
        if not host.public_ip:
            rows.append(DnsRow(host.id, False, detail="no public_ip in workshop.yaml"))
            continue
        names = list(lab_names(domain, pid, keys).values())
        refused = ""
        for name in names:
            for rr in by_name.get(_fqdn(name), []):
                mark = None
                if rr["Type"] == "TXT":
                    mark = next((m for m in (_parse_mark(v["Value"]) for v in rr.get("ResourceRecords", []))
                                 if m is not None), None)
                    if mark is None:
                        refused = f"{name} has a TXT record rodeo did not write"
                    elif mark.get("fleet") != inventory.name or mark.get("host") != host.id:
                        refused = f"{name} belongs to fleet {mark.get('fleet')!r} host {mark.get('host')!r}"
                elif rr["Type"] != "A":
                    refused = f"{name} already has a {rr['Type']} record"
            has_a = any(rr["Type"] == "A" for rr in by_name.get(_fqdn(name), []))
            has_mark = any(rr["Type"] == "TXT" for rr in by_name.get(_fqdn(name), []))
            if has_a and not has_mark:
                refused = f"{name} has an A record rodeo did not write"
        if refused:
            rows.append(DnsRow(host.id, False, tuple(names), f"refused: {refused}"))
            continue
        for name in names:
            changes.append({"Action": "UPSERT", "ResourceRecordSet": {
                "Name": name, "Type": "TXT", "TTL": TTL,
                "ResourceRecords": [{"Value": _txt_value(inventory.name, host.id, _region(inventory))}]}})
            changes.append({"Action": "UPSERT", "ResourceRecordSet": {
                "Name": name, "Type": "A", "TTL": TTL,
                "ResourceRecords": [{"Value": host.public_ip}]}})
        rows.append(DnsRow(host.id, True, tuple(names), host.public_ip))
    # TXT first in each pair: the mark exists before the A record it covers.
    return changes, rows


def plan_delete(inventory: FleetInventory, records: list[dict[str, Any]],
                host_ids: list[str] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """DELETEs for names marked for this workshop (and ``host_ids``, if given)."""
    domain = _require_player(inventory)
    suffix = "." + domain + "."
    marked: dict[str, dict[str, Any]] = {}
    for rr in records:
        name = _fqdn(rr["Name"])
        if rr["Type"] != "TXT" or not name.endswith(suffix):
            continue
        marks = [m for m in (_parse_mark(v["Value"]) for v in rr.get("ResourceRecords", [])) if m]
        # Only a TXT that is nothing but our one mark is ours to delete.
        if len(marks) != 1 or len(rr.get("ResourceRecords", [])) != 1:
            continue
        mark = marks[0]
        if mark.get("fleet") != inventory.name:
            continue
        if host_ids is not None and mark.get("host") not in set(host_ids):
            continue
        marked[name] = rr
    changes: list[dict[str, Any]] = []
    for rr in records:
        name = _fqdn(rr["Name"])
        if name in marked and rr["Type"] == "A":
            changes.append({"Action": "DELETE", "ResourceRecordSet": rr})
    # A records first, then the marks that covered them.
    changes.extend({"Action": "DELETE", "ResourceRecordSet": rr} for rr in marked.values())
    return changes, sorted(n.rstrip(".") for n in marked)


def plan_prune(domain: str, records: list[dict[str, Any]], is_live: Callable[[str, str], bool],
               default_region: str) -> tuple[list[dict[str, Any]], list[str]]:
    """DELETEs for rodeo-marked names whose A address is not live. Pure.

    ``is_live(ip, region)`` answers for one address; the region is the mark's
    (names written before marks carried one use ``default_region``). A mark
    with no A record left is removed too. Names rodeo did not mark are never
    touched.
    """
    suffix = "." + domain + "."
    by_name: dict[str, dict[str, dict[str, Any]]] = {}
    for rr in records:
        by_name.setdefault(_fqdn(rr["Name"]), {})[rr["Type"]] = rr
    changes: list[dict[str, Any]] = []
    marks: list[dict[str, Any]] = []
    names: list[str] = []
    for name, rrs in by_name.items():
        txt = rrs.get("TXT")
        mark = _own_mark(txt) if txt and name.endswith(suffix) else None
        if mark is None:
            continue
        a = rrs.get("A")
        ips = [v["Value"] for v in (a or {}).get("ResourceRecords", [])]
        region = mark.get("region") or default_region
        if ips and all(is_live(ip, region) for ip in ips):
            continue
        if a:
            changes.append({"Action": "DELETE", "ResourceRecordSet": a})
        marks.append({"Action": "DELETE", "ResourceRecordSet": txt})
        names.append(name.rstrip("."))
    # A records first, then the marks that covered them.
    return changes + marks, sorted(names)


_LIVE_STATES = ["pending", "running", "stopping", "stopped", "shutting-down"]


def live_rodeo_ips(region: str, ec2: Any | None = None) -> set[str]:
    """Public addresses of rodeo instances in ``region`` that are not terminated."""
    from ..providers.base import TAG_MANAGED_BY

    if ec2 is None:
        from ..providers.aws import _require_boto3

        _require_boto3()
        import boto3

        ec2 = boto3.client("ec2", region_name=region)
    ips: set[str] = set()
    pages = ec2.get_paginator("describe_instances").paginate(Filters=[
        {"Name": f"tag:{TAG_MANAGED_BY}", "Values": ["rodeo"]},
        {"Name": "instance-state-name", "Values": _LIVE_STATES}])
    for page in pages:
        for res in page.get("Reservations", []):
            for inst in res.get("Instances", []):
                if inst.get("PublicIpAddress"):
                    ips.add(inst["PublicIpAddress"])
    return ips


def prune(domain: str, *, default_region: str, client: Any | None = None,
          live_ips: Callable[[str], set[str]] = live_rodeo_ips) -> list[str]:
    """Remove rodeo names in ``domain`` that point at no live rodeo instance.

    Returns the names removed. Any failure to list a region's instances
    raises: nothing is removed on a guess.
    """
    client = client or route53_client()
    zone_id = find_zone(client, domain)
    seen: dict[str, set[str]] = {}

    def is_live(ip: str, region: str) -> bool:
        if not region:
            return True  # no region to ask: keep it
        if region not in seen:
            seen[region] = live_ips(region)
        return ip in seen[region]

    changes, names = plan_prune(domain, _all_records(client, zone_id), is_live, default_region)
    if changes:
        _apply(client, zone_id, changes, "rodeo fleet dns --prune")
    return names


def publish(inventory: FleetInventory, *, host_ids: list[str] | None = None,
            client: Any | None = None) -> list[DnsRow]:
    domain = _require_player(inventory)
    hosts = [h for h in inventory.hosts if host_ids is None or h.id in set(host_ids)]
    if not hosts:
        raise ConfigError("no hosts to publish (run `rodeo fleet provision` first)")
    client = client or route53_client()
    zone_id = find_zone(client, domain)
    changes, rows = plan_publish(inventory, _all_records(client, zone_id), hosts)
    if changes:
        _apply(client, zone_id, changes, f"rodeo fleet dns {inventory.name}")
    return rows


def delete(inventory: FleetInventory, *, host_ids: list[str] | None = None,
           client: Any | None = None) -> list[str]:
    """Remove this workshop's records. Returns the names removed."""
    domain = _require_player(inventory)
    client = client or route53_client()
    zone_id = find_zone(client, domain)
    changes, names = plan_delete(inventory, _all_records(client, zone_id), host_ids)
    if changes:
        _apply(client, zone_id, changes, f"rodeo fleet dns --delete {inventory.name}")
    return names
