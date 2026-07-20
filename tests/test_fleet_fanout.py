"""Tests for fleet fan-out doctor/status with mocked SSH."""
from __future__ import annotations

import json
import textwrap

from click.testing import CliRunner

from rodeo.fleet.doctor import HostDoctorResult, fleet_doctor
from rodeo.fleet.fanout import fanout
from rodeo.fleet.inventory import FleetHost, load_inventory
from rodeo.fleet.ssh_exec import RemoteResult
from rodeo.fleet.status import fleet_status


def _inv(tmp_path):
    path = tmp_path / "workshop.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: demo
            lab:
              dir: /root/lab
            hosts:
              - id: h1
                ssh: 10.0.0.1
              - id: h2
                ssh: 10.0.0.2
            """
        )
    )
    return load_inventory(path)


def test_fanout_preserves_order():
    hosts = [
        FleetHost(id="a", ssh="1"),
        FleetHost(id="b", ssh="2"),
        FleetHost(id="c", ssh="3"),
    ]
    out = fanout(hosts, lambda h: h.id.upper(), concurrency=3)
    assert out == ["A", "B", "C"]


def test_fleet_doctor_aggregates(monkeypatch, tmp_path):
    inv = _inv(tmp_path)

    def fake_run(inventory, host, argv, *, timeout=120.0):
        if host.id == "h1":
            body = {
                "host": {"cpus": 32, "has_kvm": True, "nested": True},
                "recommended_profile": "harvester",
                "profile_fits": True,
                "core_tools": {},
                "py_modules": {},
                "optional_tools": {},
            }
            return RemoteResult(host.id, 0, json.dumps(body), "")
        return RemoteResult(host.id, 255, "", "Permission denied")

    monkeypatch.setattr("rodeo.fleet.doctor.run_remote", fake_run)
    results = fleet_doctor(inv, inv.hosts, concurrency=2)
    assert results[0].ok is True
    assert results[0].report["recommended_profile"] == "harvester"
    assert results[1].ok is False
    assert "Permission denied" in (results[1].error or "")


def test_fleet_doctor_flags_reachable_but_unfit_host(monkeypatch, tmp_path):
    """SSH can succeed and rodeo doctor can return valid JSON on a host that is
    still not workshop-ready (no KVM) — local `rodeo doctor` never exits
    non-zero on its own, so fleet must not treat rc==0 as "ready"."""
    inv = _inv(tmp_path)

    def fake_run(inventory, host, argv, *, timeout=120.0):
        body = {
            "host": {"cpus": 8, "has_kvm": False, "nested": False},
            "recommended_profile": "rancher",
            "profile_fits": True,
            "core_tools": {"ansible": True},
            "py_modules": {},
            "optional_tools": {},
        }
        return RemoteResult(host.id, 0, json.dumps(body), "")

    monkeypatch.setattr("rodeo.fleet.doctor.run_remote", fake_run)
    results = fleet_doctor(inv, [inv.hosts[0]], concurrency=1)
    assert results[0].ok is False
    assert results[0].report is not None  # kept for JSON/table detail, unlike SSH failures
    assert "no /dev/kvm" in (results[0].error or "")
    assert "nested virtualization" in (results[0].error or "")


def test_fleet_status_bad_json(monkeypatch, tmp_path):
    inv = _inv(tmp_path)

    def fake_run(inventory, host, argv, *, timeout=120.0):
        return RemoteResult(host.id, 0, "not-json", "")

    monkeypatch.setattr("rodeo.fleet.status.run_remote", fake_run)
    results = fleet_status(inv, [inv.hosts[0]], concurrency=1)
    assert results[0].ok is False
    assert "invalid JSON" in (results[0].error or "")


def test_fleet_doctor_cli_json(monkeypatch, tmp_path):
    inv_path = tmp_path / "workshop.yaml"
    inv_path.write_text(
        textwrap.dedent(
            """
            name: cli-demo
            lab:
              dir: /root/lab
            hosts:
              - id: h1
                ssh: 10.0.0.1
            """
        )
    )

    def fake_fleet_doctor(inventory, hosts, *, concurrency=8, timeout=120.0):
        return [
            HostDoctorResult(
                id="h1",
                ok=True,
                error=None,
                report={
                    "recommended_profile": "rancher",
                    "profile_fits": True,
                    "host": {"cpus": 8},
                },
            )
        ]

    monkeypatch.setattr("rodeo.commands.fleet_cmd.fleet_doctor", fake_fleet_doctor)

    from rodeo.cli import cli

    result = CliRunner().invoke(
        cli,
        ["fleet", "doctor", "-f", str(inv_path), "--output", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["workshop"] == "cli-demo"
    assert payload["hosts"][0]["ok"] is True
    assert payload["hosts"][0]["report"]["recommended_profile"] == "rancher"


def test_fleet_doctor_cli_exit_1_on_failure(monkeypatch, tmp_path):
    inv_path = tmp_path / "workshop.yaml"
    inv_path.write_text(
        textwrap.dedent(
            """
            name: cli-demo
            lab:
              dir: /root/lab
            hosts:
              - id: h1
                ssh: 10.0.0.1
            """
        )
    )
    monkeypatch.setattr(
        "rodeo.commands.fleet_cmd.fleet_doctor",
        lambda *a, **k: [HostDoctorResult("h1", False, "ssh failed", None)],
    )
    from rodeo.cli import cli

    result = CliRunner().invoke(cli, ["fleet", "doctor", "-f", str(inv_path)])
    assert result.exit_code == 1
