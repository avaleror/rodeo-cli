"""Tests for fleet job file and access sheet."""
from __future__ import annotations

from rodeo.fleet.access import access_payload, fleet_access
from rodeo.fleet.inventory import FleetHost, FleetInventory
from rodeo.fleet.job import job_path_for, load_job, new_job, save_job


def test_job_path_for(tmp_path):
    inv = tmp_path / "workshop.yaml"
    assert job_path_for(inv) == tmp_path / "workshop.job.yaml"


def test_job_roundtrip(tmp_path):
    inv = tmp_path / "workshop.yaml"
    inv.write_text("name: x\n")
    job = new_job(
        workshop="demo",
        inventory_path=inv,
        concurrency=4,
        host_ids=["a", "b"],
    )
    job.set_host("a", state="failed", last_error="boom")
    path = job_path_for(inv)
    save_job(job, path)
    loaded = load_job(path)
    assert loaded.workshop == "demo"
    assert loaded.failed_ids() == ["a"]
    assert loaded.hosts["b"].state == "pending"


def test_access_urls():
    inv = FleetInventory(
        name="demo",
        lab_dir="/root/lab",
        defaults={},
        hosts=[],
        harvester_ui_port=8443,
        rancher_ui_port=30002,
    )
    hosts = [
        FleetHost(id="s1", ssh="root@203.0.113.10", public_ip="203.0.113.10"),
        FleetHost(id="s2", ssh="10.0.0.5"),  # derive from ssh
    ]
    rows = fleet_access(inv, hosts)
    assert rows[0].harvester_url == "https://203.0.113.10:8443"
    assert rows[0].rancher_url == "https://203.0.113.10:30002"
    assert rows[1].harvester_url == "https://10.0.0.5:8443"
    payload = access_payload("demo", rows)
    assert payload["hosts"][0]["id"] == "s1"


def test_access_urls_filtered_by_lab_components():
    """A harvester-only workshop shouldn't advertise a Rancher URL that
    doesn't exist on the host — lab.components lets the operator say so."""
    inv = FleetInventory(
        name="demo",
        lab_dir="/root/lab",
        defaults={},
        hosts=[],
        harvester_ui_port=8443,
        rancher_ui_port=30002,
        lab_components=["harvester"],
    )
    host = FleetHost(id="s1", ssh="root@203.0.113.10", public_ip="203.0.113.10")
    row = fleet_access(inv, [host])[0]
    assert row.harvester_url == "https://203.0.113.10:8443"
    assert row.rancher_url is None
