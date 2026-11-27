"""rodeo fleet dns / cert: Route 53 records and the wildcard certificate (fake AWS, no network)."""
from __future__ import annotations

import json

import pytest
import yaml
from click.testing import CliRunner

from rodeo.config import ConfigError
from rodeo.fleet import cert as fcert
from rodeo.fleet import dns
from rodeo.fleet import player as fpl
from rodeo.fleet.inventory import ensure_player_ids, load_inventory, player_id
from rodeo.fleet.ssh_exec import RemoteResult

ZONE = "rodeocli.com"
SHA = "77022fd51ab5797cd201b9512fe44af3dc2ae9f6"


class FakeRoute53:
    """Just enough of boto3's route53 client, with Route 53's own rules."""

    def __init__(self, records=None, zones=None):
        self.zones = zones if zones is not None else [
            {"Id": "/hostedzone/ZTEST", "Name": f"{ZONE}.", "Config": {"PrivateZone": False}}]
        self.records = {(r["Name"], r["Type"]): r for r in (records or [])}
        self.calls = []
        base = [{"Name": f"{ZONE}.", "Type": "NS", "TTL": 172800, "ResourceRecords": [{"Value": "ns-1.awsdns."}]},
                {"Name": f"{ZONE}.", "Type": "SOA", "TTL": 900, "ResourceRecords": [{"Value": "soa"}]}]
        for r in base:
            self.records.setdefault((r["Name"], r["Type"]), r)

    def list_hosted_zones_by_name(self, DNSName, MaxItems):
        return {"HostedZones": self.zones}

    def get_paginator(self, name):
        fake = self

        class P:
            def paginate(self, HostedZoneId):
                assert HostedZoneId == "ZTEST"
                yield {"ResourceRecordSets": list(fake.records.values())}
        return P()

    def change_resource_record_sets(self, HostedZoneId, ChangeBatch):
        assert HostedZoneId == "ZTEST"
        self.calls.append(ChangeBatch["Changes"])
        for ch in ChangeBatch["Changes"]:
            rr = dict(ch["ResourceRecordSet"])
            name = rr["Name"].rstrip(".") + "."
            rr["Name"] = name
            key = (name, rr["Type"])
            assert name.endswith(f".{ZONE}."), name
            if ch["Action"] == "UPSERT":
                self.records[key] = rr
            elif ch["Action"] == "DELETE":
                assert self.records.get(key) == rr, "DELETE must match the record exactly"
                del self.records[key]
        return {"ChangeInfo": {"Id": "/change/C1"}}

    def get_waiter(self, name):
        class W:
            def wait(self, **kw):
                pass
        return W()


def _workshop(tmp_path, hosts=2, name="ws", player=True):
    data = {
        "name": name, "lab": {"dir": "/root/lab", "profile": "virt-rodeo"},
        "hosts": [{"id": f"lab-{i}", "ssh": f"10.0.0.{i}", "public_ip": f"203.0.113.{i}"}
                  for i in range(1, hosts + 1)],
    }
    if player:
        data["player"] = {"domain": ZONE}
    p = tmp_path / "workshop.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def _ready(tmp_path, **kw):
    p = _workshop(tmp_path, **kw)
    ensure_player_ids(p)
    return p, load_inventory(p)


def test_player_block_is_validated(tmp_path):
    p = _workshop(tmp_path)
    assert load_inventory(p).player.domain == ZONE
    for bad in ("x", "1-2-3-4.sslip.io", ""):
        raw = yaml.safe_load(p.read_text())
        raw["player"] = {"domain": bad}
        p.write_text(yaml.safe_dump(raw))
        with pytest.raises(ConfigError, match="player.domain"):
            load_inventory(p)


def test_player_ids_are_random_unique_and_stable(tmp_path):
    p = _workshop(tmp_path, hosts=5)
    first = ensure_player_ids(p)
    assert len(set(first.values())) == 5
    assert all(len(v) == 8 and v[0].isalpha() and v.isalnum() and v.islower() for v in first.values())
    assert ensure_player_ids(p) == first  # kept on re-run
    inv = load_inventory(p)
    assert {h.id: player_id(h) for h in inv.hosts} == first


def test_publish_writes_three_marked_names_per_lab_and_is_idempotent(tmp_path):
    _, inv = _ready(tmp_path)
    client = FakeRoute53()
    rows = dns.publish(inv, client=client)
    assert all(r.ok for r in rows)
    pid = player_id(inv.hosts[0])
    for name in (f"{pid}.{ZONE}.", f"hv-{pid}.{ZONE}.", f"rancher-{pid}.{ZONE}."):
        assert client.records[(name, "A")]["ResourceRecords"] == [{"Value": "203.0.113.1"}]
        assert client.records[(name, "TXT")]["ResourceRecords"] == [
            {"Value": '"managed-by=rodeo fleet=ws host=lab-1"'}]
    count = len(client.records)
    assert count == 2 + 2 * 3 * 2
    assert all(r.ok for r in dns.publish(inv, client=client))
    assert len(client.records) == count
    # Never a wildcard record, never anything on the apex but NS + SOA.
    assert not any(n.startswith("*") for n, _ in client.records)
    assert {t for n, t in client.records if n == f"{ZONE}."} == {"NS", "SOA"}


def test_publish_moves_a_lab_to_its_new_address(tmp_path):
    p, inv = _ready(tmp_path, hosts=1)
    client = FakeRoute53()
    dns.publish(inv, client=client)
    raw = yaml.safe_load(p.read_text())
    raw["hosts"][0]["public_ip"] = "198.51.100.9"
    p.write_text(yaml.safe_dump(raw))
    dns.publish(load_inventory(p), client=client)
    pid = player_id(inv.hosts[0])
    assert client.records[(f"{pid}.{ZONE}.", "A")]["ResourceRecords"] == [{"Value": "198.51.100.9"}]


@pytest.mark.parametrize("foreign", [
    {"Type": "A", "TTL": 300, "ResourceRecords": [{"Value": "192.0.2.1"}]},
    {"Type": "CNAME", "TTL": 300, "ResourceRecords": [{"Value": "elsewhere.example."}]},
    {"Type": "TXT", "TTL": 300, "ResourceRecords": [{"Value": '"v=spf1 -all"'}]},
    {"Type": "TXT", "TTL": 60, "ResourceRecords": [{"Value": '"managed-by=rodeo fleet=other host=lab-1"'}]},
])
def test_publish_refuses_names_rodeo_did_not_write_for_this_lab(tmp_path, foreign):
    _, inv = _ready(tmp_path, hosts=1)
    pid = player_id(inv.hosts[0])
    rec = {"Name": f"hv-{pid}.{ZONE}.", **foreign}
    client = FakeRoute53([rec])
    rows = dns.publish(inv, client=client)
    assert not rows[0].ok and "refused" in rows[0].detail
    assert client.calls == []
    assert client.records[(rec["Name"], rec["Type"])] == rec


def test_delete_removes_only_this_workshops_marked_names(tmp_path):
    _, inv = _ready(tmp_path, hosts=2)
    other = tmp_path / "other"
    other.mkdir()
    _, inv_other = _ready(other, hosts=1, name="other-ws")
    hand = {"Name": f"www.{ZONE}.", "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": "192.0.2.7"}]}
    client = FakeRoute53([hand])
    dns.publish(inv, client=client)
    dns.publish(inv_other, client=client)
    before = dict(client.records)

    removed = dns.delete(inv, host_ids=["lab-1"], client=client)
    pid1, pid2 = player_id(inv.hosts[0]), player_id(inv.hosts[1])
    assert removed == sorted([f"{pid1}.{ZONE}", f"hv-{pid1}.{ZONE}", f"rancher-{pid1}.{ZONE}"])
    assert (f"{pid2}.{ZONE}.", "A") in client.records

    dns.delete(inv, client=client)
    left = set(client.records)
    assert not any(pid in n for n, _ in left for pid in (pid1, pid2))
    assert (hand["Name"], "A") in left
    assert {k for k in before if player_id(inv_other.hosts[0]) in k[0]} <= left
    assert dns.delete(inv, client=client) == []  # nothing left: no call
    assert len(client.calls) == 4


def test_zone_must_match_exactly_and_be_public():
    client = FakeRoute53(zones=[{"Id": "/hostedzone/ZX", "Name": f"sub.{ZONE}.", "Config": {}},
                                {"Id": "/hostedzone/ZP", "Name": f"{ZONE}.", "Config": {"PrivateZone": True}}])
    with pytest.raises(ConfigError, match="no public Route 53 hosted zone"):
        dns.find_zone(client, ZONE)


def test_dns_command_writes_ids_then_records(tmp_path, monkeypatch):
    from rodeo.cli import cli

    p = _workshop(tmp_path)
    client = FakeRoute53()
    monkeypatch.setattr(dns, "route53_client", lambda: client)
    res = CliRunner().invoke(cli, ["fleet", "dns", "-f", str(p), "--output", "json"])
    assert res.exit_code == 0, res.output
    rows = json.loads(res.output)
    assert [r["ok"] for r in rows] == [True, True]
    assert all(player_id(h) for h in load_inventory(p).hosts)
    res = CliRunner().invoke(cli, ["fleet", "dns", "-f", str(p), "--delete", "--output", "json"])
    assert res.exit_code == 0 and len(json.loads(res.output)["deleted"]) == 6


def _rr(name, typ, value):
    return {"Name": f"{name}.{ZONE}.", "Type": typ, "TTL": 60, "ResourceRecords": [{"Value": value}]}


def _lab(name, ip, mark):
    return [_rr(name, "A", ip), _rr(name, "TXT", f'"managed-by=rodeo {mark}"')]


def test_prune_removes_names_of_labs_that_are_gone_and_nothing_else():
    records = [
        *_lab("live1", "198.51.100.1", "fleet=a host=h1 region=eu-north-1"),
        *_lab("dead1", "198.51.100.2", "fleet=a host=h2 region=eu-north-1"),
        *_lab("dead2", "198.51.100.3", "fleet=b host=h1"),               # older mark: no region
        *_lab("west1", "198.51.100.1", "fleet=c host=h1 region=us-west-2"),  # same IP, other region
        _rr("orphan", "TXT", '"managed-by=rodeo fleet=a host=h3"'),       # mark with no A left
        _rr("www", "A", "198.51.100.9"),                                   # not ours: no mark
        _rr("hand", "A", "198.51.100.8"), _rr("hand", "TXT", '"v=spf1 -all"'),
        _rr("mixed", "A", "198.51.100.7"),
        {"Name": f"mixed.{ZONE}.", "Type": "TXT", "TTL": 60,               # mark plus someone else's value
         "ResourceRecords": [{"Value": '"managed-by=rodeo fleet=a host=h9"'}, {"Value": '"other"'}]},
    ]
    client = FakeRoute53(records)
    asked = []

    def live(region):
        asked.append(region)
        return {"eu-north-1": {"198.51.100.1", "198.51.100.7"}, "us-west-2": set()}[region]

    removed = dns.prune(ZONE, default_region="eu-north-1", client=client, live_ips=live)
    assert removed == sorted(f"{n}.{ZONE}" for n in ("dead1", "dead2", "west1", "orphan"))
    left = {n for n, _ in client.records}
    assert {f"live1.{ZONE}.", f"www.{ZONE}.", f"hand.{ZONE}.", f"mixed.{ZONE}."} <= left
    assert not left & {f"{n}.{ZONE}." for n in ("dead1", "dead2", "west1", "orphan")}
    assert sorted(asked) == ["eu-north-1", "us-west-2"]  # one lookup per region
    # Each A record goes before the mark that covered it.
    kinds = [(c["ResourceRecordSet"]["Name"], c["ResourceRecordSet"]["Type"]) for c in client.calls[0]]
    assert kinds.index((f"dead1.{ZONE}.", "A")) < kinds.index((f"dead1.{ZONE}.", "TXT"))
    assert dns.prune(ZONE, default_region="eu-north-1", client=client, live_ips=live) == []
    assert len(client.calls) == 1


def test_prune_removes_nothing_when_it_cannot_list_instances():
    client = FakeRoute53(_lab("dead1", "198.51.100.2", "fleet=a host=h2 region=eu-north-1"))

    def broken(region):
        raise RuntimeError("expired credentials")

    with pytest.raises(RuntimeError):
        dns.prune(ZONE, default_region="eu-north-1", client=client, live_ips=broken)
    assert client.calls == []


def test_live_rodeo_ips_asks_for_rodeo_instances_not_terminated():
    class Ec2:
        def get_paginator(self, name):
            assert name == "describe_instances"
            outer = self

            class P:
                def paginate(self, Filters):
                    outer.filters = Filters
                    yield {"Reservations": [{"Instances": [{"PublicIpAddress": "198.51.100.1"}, {}]}]}
            return P()

    ec2 = Ec2()
    assert dns.live_rodeo_ips("eu-north-1", ec2=ec2) == {"198.51.100.1"}
    f = {x["Name"]: x["Values"] for x in ec2.filters}
    assert f["tag:ManagedBy"] == ["rodeo"] and "terminated" not in f["instance-state-name"]
    assert "running" in f["instance-state-name"] and "stopped" in f["instance-state-name"]


def test_publish_marks_carry_the_region(tmp_path):
    p = _workshop(tmp_path, hosts=1)
    raw = yaml.safe_load(p.read_text())
    raw["provider"] = {"type": "aws", "region": "eu-north-1", "count": 1}
    p.write_text(yaml.safe_dump(raw))
    ensure_player_ids(p)
    inv = load_inventory(p)
    client = FakeRoute53()
    dns.publish(inv, client=client)
    pid = player_id(inv.hosts[0])
    assert client.records[(f"{pid}.{ZONE}.", "TXT")]["ResourceRecords"] == [
        {"Value": '"managed-by=rodeo fleet=ws host=lab-1 region=eu-north-1"'}]
    # delete still finds them by fleet and host.
    assert len(dns.delete(inv, client=client)) == 3


def test_dns_command_prunes_alone_with_prune(tmp_path, monkeypatch):
    from rodeo.cli import cli

    p = _workshop(tmp_path)
    client = FakeRoute53(_lab("dead1", "198.51.100.2", "fleet=old host=h1 region=eu-north-1"))
    monkeypatch.setattr(dns, "route53_client", lambda: client)
    monkeypatch.setattr(dns.prune, "__kwdefaults__", {**dns.prune.__kwdefaults__, "live_ips": lambda r: set()})
    res = CliRunner().invoke(cli, ["fleet", "dns", "-f", str(p), "--prune", "--output", "json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output) == {"pruned": [f"dead1.{ZONE}"]}
    assert not any(player_id(h) for h in load_inventory(p).hosts)  # nothing published


# ---------------------------------------------------------------- certificate

def test_lego_is_pinned_and_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _, inv = _ready(tmp_path)
    argv = fcert.lego_argv(fcert.lego_path(), inv)
    assert argv[1] == "run" and "--accept-tos" in argv
    assert argv[argv.index("--dns") + 1] == "route53"
    assert argv[argv.index("--domains") + 1] == f"*.{ZONE}"
    assert "--server" not in argv
    assert all(len(v) == 64 for v in fcert.LEGO_SHA256.values())
    assert fcert.lego_path().name == f"lego-{fcert.LEGO_VERSION}"
    assert "--dns.propagation.disable-rns" in argv
    assert fcert.cert_dir(ZONE, True) != fcert.cert_dir(ZONE)


def _bundle() -> str:
    return ("-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n"
            "-----BEGIN CERTIFICATE-----\nBBB\n-----END CERTIFICATE-----\n"
            "-----BEGIN EC PRIVATE KEY-----\nKKK\n-----END EC PRIVATE KEY-----\n")  # gitleaks:allow (fake test key)


def test_player_setup_pushes_the_cert_on_stdin_never_argv(tmp_path, monkeypatch):
    _, inv = _ready(tmp_path)
    monkeypatch.setattr(fcert, "bundle_for_push", lambda domain, staging=False: _bundle())
    seen = []

    def fake_remote(inventory, host, argv, *, timeout=120.0, as_root=True, stdin=None):
        seen.append((host, argv, stdin))
        return RemoteResult(host.id, 0, json.dumps({"player": "x", "chapters": 7}) + "\n", "")

    monkeypatch.setattr(fpl, "run_remote", fake_remote)
    rows = fpl.fleet_player_setup(inv, track_ref=SHA)
    assert all(r.ok for r in rows)
    for host, argv, stdin in seen:
        assert argv[-5:] == ["--domain", ZONE, "--label", player_id(host), "--tls-stdin"]
        assert stdin == _bundle()
        assert "KKK" not in " ".join(argv)


def test_player_setup_without_ids_fails_per_host(tmp_path, monkeypatch):
    inv = load_inventory(_workshop(tmp_path))
    monkeypatch.setattr(fcert, "bundle_for_push", lambda domain, staging=False: _bundle())
    monkeypatch.setattr(fpl, "run_remote", lambda *a, **k: pytest.fail("must not run"))
    rows = fpl.fleet_player_setup(inv, track_ref=SHA)
    assert not any(r.ok for r in rows) and "fleet dns" in rows[0].error


def test_bundle_for_push_refuses_a_missing_or_expiring_cert(tmp_path, monkeypatch):
    monkeypatch.setattr(fcert, "current", lambda domain, staging=False: None)
    with pytest.raises(ConfigError, match="fleet cert"):
        fcert.bundle_for_push(ZONE)
    import time

    monkeypatch.setattr(fcert, "current", lambda domain, staging=False: fcert.CertFiles(tmp_path / "c", tmp_path / "k",
                                                                         time.time() + 86400))
    with pytest.raises(ConfigError, match="expires"):
        fcert.bundle_for_push(ZONE)


# ---------------------------------------------------------------- the claim portal's own name
def _portal_ws(tmp_path, name="ws", ip="198.51.100.40"):
    p = _workshop(tmp_path, hosts=1, name=name)
    raw = yaml.safe_load(p.read_text())
    raw["provider"] = {"type": "aws", "region": "eu-north-1", "count": 1, "student_access": "open"}
    raw["portal"] = {"enabled": True, "ssh": ip, "public_ip": ip}
    p.write_text(yaml.safe_dump(raw))
    return p


def test_every_portal_gets_its_own_random_name_under_the_zone(tmp_path):
    from rodeo.fleet.inventory import ensure_portal_id, portal_fqdn

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    pa, pb = _portal_ws(a, "ws-a"), _portal_ws(b, "ws-b", "198.51.100.41")
    ida, idb = ensure_portal_id(pa), ensure_portal_id(pb)
    assert ida != idb and ensure_portal_id(pa) == ida  # kept once written
    ia, ib = load_inventory(pa), load_inventory(pb)
    assert portal_fqdn(ia) == f"portal-{ida}.{ZONE}" and portal_fqdn(ib) == f"portal-{idb}.{ZONE}"
    # Two portals live at once: both names, each marked for its own workshop.
    client = FakeRoute53()
    assert dns.publish_portal(ia, client=client).ok and dns.publish_portal(ib, client=client).ok
    assert client.records[(f"portal-{ida}.{ZONE}.", "A")]["ResourceRecords"] == [{"Value": "198.51.100.40"}]
    assert client.records[(f"portal-{idb}.{ZONE}.", "TXT")]["ResourceRecords"] == [
        {"Value": '"managed-by=rodeo fleet=ws-b host=portal region=eu-north-1"'}]
    # Deleting one workshop's names leaves the other portal alone.
    dns.delete(ia, client=client)
    assert (f"portal-{ida}.{ZONE}.", "A") not in client.records
    assert (f"portal-{idb}.{ZONE}.", "A") in client.records


def test_a_portal_never_takes_a_name_that_is_not_its_own(tmp_path):
    from rodeo.fleet.inventory import ensure_portal_id

    p = _portal_ws(tmp_path)
    pid = ensure_portal_id(p)
    inv = load_inventory(p)
    name = f"portal-{pid}.{ZONE}."
    for foreign in ([{"Name": name, "Type": "TXT", "TTL": 60,
                      "ResourceRecords": [{"Value": '"managed-by=rodeo fleet=other host=portal"'}]}],
                    [{"Name": name, "Type": "A", "TTL": 60, "ResourceRecords": [{"Value": "192.0.2.1"}]}]):
        row = dns.publish_portal(inv, client=FakeRoute53(foreign))
        assert not row.ok and "refused" in row.detail


def test_without_a_zone_or_with_a_hostname_the_portal_name_is_as_before(tmp_path):
    from rodeo.fleet.inventory import portal_fqdn

    p = _portal_ws(tmp_path)
    raw = yaml.safe_load(p.read_text())
    del raw["player"]
    p.write_text(yaml.safe_dump(raw))
    assert portal_fqdn(load_inventory(p)) == "portal-198-51-100-40.sslip.io"
    raw["portal"]["hostname"] = "labs.example.com"
    p.write_text(yaml.safe_dump(raw))
    assert portal_fqdn(load_inventory(p)) == "labs.example.com"


def test_portal_up_writes_the_portal_name_before_installing(tmp_path, monkeypatch):
    from rodeo.cli import cli

    p = _portal_ws(tmp_path)
    client = FakeRoute53()
    order = []
    monkeypatch.setattr(dns, "route53_client", lambda: client)
    real = dns.publish_portal
    monkeypatch.setattr(dns, "publish_portal", lambda inv, **k: order.append("dns") or real(inv, **k))
    monkeypatch.setattr("rodeo.commands.fleet_portal_cmd.portal_up",
                        lambda inv, wait=True: order.append("install") or f"https://portal-{inv.portal.portal_id}.{ZONE}")
    res = CliRunner().invoke(cli, ["fleet", "portal", "up", "-f", str(p), "--no-wait"])
    assert res.exit_code == 0, res.output
    assert order == ["dns", "install"]
    pid = load_inventory(p).portal.portal_id
    assert f"portal-{pid}.{ZONE}" in res.output and (f"portal-{pid}.{ZONE}.", "A") in client.records


def test_a_new_portal_id_skips_names_already_in_the_zone(tmp_path, monkeypatch):
    from rodeo.fleet import inventory as finv

    p = _portal_ws(tmp_path)
    ids = iter(["aaaaaaa1", "bbbbbbb2"])
    monkeypatch.setattr(finv, "_new_id", lambda used: next(ids))
    client = FakeRoute53([{"Name": f"portal-aaaaaaa1.{ZONE}.", "Type": "A", "TTL": 60,
                           "ResourceRecords": [{"Value": "192.0.2.9"}]}])
    taken = dns.portal_name_taken(load_inventory(p), client=client)
    assert finv.ensure_portal_id(p, taken=taken) == "bbbbbbb2"
