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


def _bundle() -> str:
    return ("-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n"
            "-----BEGIN CERTIFICATE-----\nBBB\n-----END CERTIFICATE-----\n"
            "-----BEGIN EC PRIVATE KEY-----\nKKK\n-----END EC PRIVATE KEY-----\n")  # gitleaks:allow (fake test key)


def test_player_setup_pushes_the_cert_on_stdin_never_argv(tmp_path, monkeypatch):
    _, inv = _ready(tmp_path)
    monkeypatch.setattr(fcert, "bundle_for_push", lambda domain: _bundle())
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
    monkeypatch.setattr(fcert, "bundle_for_push", lambda domain: _bundle())
    monkeypatch.setattr(fpl, "run_remote", lambda *a, **k: pytest.fail("must not run"))
    rows = fpl.fleet_player_setup(inv, track_ref=SHA)
    assert not any(r.ok for r in rows) and "fleet dns" in rows[0].error


def test_bundle_for_push_refuses_a_missing_or_expiring_cert(tmp_path, monkeypatch):
    monkeypatch.setattr(fcert, "current", lambda domain: None)
    with pytest.raises(ConfigError, match="fleet cert"):
        fcert.bundle_for_push(ZONE)
    import time

    monkeypatch.setattr(fcert, "current", lambda domain: fcert.CertFiles(tmp_path / "c", tmp_path / "k",
                                                                         time.time() + 86400))
    with pytest.raises(ConfigError, match="expires"):
        fcert.bundle_for_push(ZONE)
