"""Deploy progress on the instructor page (`rodeo fleet portal publish --watch`)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from rodeo.fleet import portal as fp
from rodeo.fleet.inventory import load_inventory
from rodeo.fleet.job import HostJobRecord, load_job, new_job, save_job
from rodeo.fleet.status import HostStatusResult
from rodeo.portal import claims
from rodeo.portal.db import connect, migrate
from rodeo.portal.web import progress_section

PHASES_SORTED = ["apply", "cluster", "custom_scripts", "finalise", "kvm_host", "pxe_server",
                 "rancher", "vms"]


def _status(hid, done: set[str], ok=True):
    if not ok:
        return HostStatusResult(id=hid, ok=False, error="ssh: timeout", report=None)
    phases = {}
    for name in PHASES_SORTED:  # rodeo status returns them sorted by name
        info = {"completed": name in done}
        if name in ("apply", "custom_scripts"):
            info["no_cache"] = True
        if name in done:
            info["timestamp"] = f"2026-09-30T10:{len(done):02d}:00+00:00"
        phases[name] = info
    return HostStatusResult(id=hid, ok=True, error=None,
                            report={"phases": phases, "vip_reachable": "cluster" in done})


def test_progress_orders_phases_and_counts_only_cacheable():
    p = fp.host_progress(_status("s1", {"kvm_host", "vms"}), HostJobRecord(state="running",
                         started_at="2026-09-30T09:50:00+00:00"))
    assert [ph["name"] for ph in p["phases"]] == ["kvm_host", "vms", "pxe_server", "cluster",
                                                   "rancher", "finalise"]
    assert (p["done"], p["total"], p["current"], p["state"]) == (2, 6, "pxe_server", "running")
    assert p["error"] is None


def test_progress_complete_is_ok_with_finish_time():
    all6 = {"kvm_host", "vms", "pxe_server", "cluster", "rancher", "finalise"}
    p = fp.host_progress(_status("s1", all6), None)
    assert p["state"] == "ok" and p["current"] is None and p["finished_at"]


def test_progress_unreachable_and_failed_keep_the_error_short():
    p = fp.host_progress(_status("s1", set(), ok=False), None)
    assert p["state"] == "unreachable" and "not reachable" in p["error"]
    p = fp.host_progress(_status("s1", {"kvm_host"}),
                         HostJobRecord(state="failed", last_error="phase vms: " + "x" * 900))
    assert p["state"] == "failed" and len(p["error"]) == 300


# ---------------------------------------------------------------- watch loop
@pytest.fixture
def watch_env(tmp_path, monkeypatch):
    data = {
        "name": "ws", "lab": {"dir": "/root/lab", "profile": "harvester"},
        "provider": {"type": "aws", "region": "r", "subnet_id": "s", "instance_type": "m8id.8xlarge",
                     "student_access": "open"},
        "hosts": [{"id": "s1", "ssh": "10.0.0.1", "public_ip": "10.0.0.1"},
                  {"id": "s2", "ssh": "10.0.0.2", "public_ip": "10.0.0.2"}],
        "portal": {"enabled": True, "ssh": "10.9.9.9", "public_ip": "10.9.9.9"},
    }
    inv_path = tmp_path / "workshop.yaml"
    inv_path.write_text(yaml.safe_dump(data))
    job = new_job(workshop="ws", inventory_path=inv_path, concurrency=2, host_ids=["s1", "s2"])
    for hid in ("s1", "s2"):
        job.set_host(hid, state="running", started_at="2026-09-30T10:00:00+00:00")
    save_job(job, tmp_path / "workshop.job.yaml")
    all6 = {"kvm_host", "vms", "pxe_server", "cluster", "rancher", "finalise"}
    script = iter([
        [_status("s1", {"kvm_host"}), _status("s2", set(), ok=False)],
        [_status("s1", all6), _status("s2", {"kvm_host", "vms"})],
        [_status("s1", all6), _status("s2", all6)],
    ])
    monkeypatch.setattr("rodeo.fleet.status.fleet_status", lambda *a, **k: next(script))
    lab_records: list[str] = []

    def fake_record(inventory, host, *, timeout):
        lab_records.append(host.id)
        return ({"id": host.id, "ready": True, "data": {"components": ["creds"]}},
                fp.PublishRow(host.id, True, None))

    monkeypatch.setattr(fp, "_lab_record", fake_record)
    pushes: list[tuple[str, object]] = []
    monkeypatch.setattr(fp, "portal_admin",
                        lambda inv, args, stdin=None, raw=False: pushes.append((args[0], json.loads(stdin))))
    return load_inventory(inv_path), inv_path, pushes, lab_records


def test_watch_publishes_each_lab_once_when_ready_and_stops(watch_env):
    inv, inv_path, pushes, lab_records = watch_env
    ticks = []
    final = fp.portal_watch(inv, inv_path, interval=0, sleep=lambda s: None,
                            on_tick=lambda t, p: ticks.append(t))
    assert final.done and len(ticks) == 3
    assert lab_records == ["s1", "s2"], "each lab's credentials are read exactly once"
    imports = [body for kind, body in pushes if kind == "import"]
    # tick 1: every lab appears as building, with its inventory order
    assert imports[0] == [{"id": "s1", "ready": False, "data": {}, "ord": 0},
                          {"id": "s2", "ready": False, "data": {}, "ord": 1}]
    assert imports[1] == [{"id": "s1", "ready": True, "data": {"components": ["creds"]}, "ord": 0}]
    assert imports[2][0]["id"] == "s2" and imports[2][0]["ord"] == 1
    progress = [body for kind, body in pushes if kind == "progress"]
    assert progress[0]["s2"]["state"] == "unreachable"
    assert progress[1]["s2"]["current"] == "pxe_server"
    assert ticks[1].newly_published == ["s1"]


def test_watch_keeps_job_file_in_sync(watch_env, tmp_path):
    inv, inv_path, _, _ = watch_env
    fp.portal_watch(inv, inv_path, interval=0, sleep=lambda s: None)
    job = load_job(tmp_path / "workshop.job.yaml")
    assert {h: r.state for h, r in job.hosts.items()} == {"s1": "ok", "s2": "ok"}


def test_progress_payload_never_contains_credentials(watch_env):
    inv, inv_path, pushes, _ = watch_env
    fp.portal_watch(inv, inv_path, interval=0, sleep=lambda s: None)
    assert "creds" not in json.dumps([b for k, b in pushes if k == "progress"])


# ---------------------------------------------------------------- portal side
def test_progress_section_renders_bar_elapsed_and_stale_warning():
    now = datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc)
    prog = {"s1": {"state": "running", "done": 3, "total": 6, "current": "cluster",
                   "started_at": "2026-09-30T10:20:00+00:00",
                   "phases": [{"name": "kvm_host", "done": True}], "error": None},
            "s2": {"state": "failed", "done": 1, "total": 6, "error": "phase vms: <boom>"}}
    html = progress_section(prog, (now - timedelta(seconds=40)).isoformat(), ["s1", "s2"], now=now)
    assert "width:50%" in html and "40 min" in html and "cluster" in html
    assert "Updated 40 s ago" in html and "&lt;boom&gt;" in html
    stale = progress_section(prog, (now - timedelta(minutes=10)).isoformat(), ["s1"], now=now)
    assert "still running?" in stale
    assert progress_section({}, None, []) == ""


def test_v1_database_is_migrated_without_losing_claims(tmp_path):
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.executescript("""
      CREATE TABLE labs(id TEXT PRIMARY KEY, ord INTEGER NOT NULL, data TEXT NOT NULL, ready INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE claims(lab_id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL, pin_hash TEXT, token_hash TEXT UNIQUE NOT NULL, claimed_at TEXT NOT NULL,
        opened_at TEXT, bad_pins INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE settings(k TEXT PRIMARY KEY, v TEXT NOT NULL);
      INSERT INTO labs VALUES('s1', 0, '{}', 1);
      INSERT INTO claims VALUES('s1','a@x.io','A','open',NULL,'h','t',NULL,0);
      PRAGMA user_version=1;
    """)
    con.close()
    migrate(str(p))
    c = connect(str(p))
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    assert claims.status_rows(c)[0]["email"] == "a@x.io"
    claims.set_progress(c, {"s1": {"state": "ok"}})
    prog, at = claims.progress(c)
    assert prog == {"s1": {"state": "ok"}} and at
    c.close()
