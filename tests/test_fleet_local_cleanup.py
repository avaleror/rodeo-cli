"""Laptop leftovers of terminated cloud hosts are removed with them."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from rodeo.fleet.job import job_path_for
from rodeo.fleet.local_cleanup import forget_cloud_hosts, forget_single_host, workshop_state_dir
from rodeo.fleet.ssh_exec import known_hosts_path
from rodeo.providers.base import DeprovisionResult

WS = "demo"


def _host_key(tmp_path: Path) -> str:
    key = tmp_path / "hostkey"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    return key.with_suffix(".pub").read_text().split()[1]


def _setup(tmp_path: Path, *, byo: bool = False, portal_ip: str = "") -> Path:
    hosts = [
        {"id": "student-01", "ssh": "203.0.113.1", "public_ip": "203.0.113.1",
         "labels": {"provider": "aws", "provider_id": "i-1"}},
        {"id": "student-02", "ssh": "203.0.113.2", "public_ip": "203.0.113.2",
         "labels": {"provider": "aws", "provider_id": "i-2"}},
    ]
    if byo:
        hosts.append({"id": "lab-box", "ssh": "root@198.51.100.9"})
    plan = {"name": WS, "lab": {"dir": "/root/lab", "profile": "harvester"},
            "provider": {"type": "aws", "region": "eu-north-1", "subnet_id": "subnet-1"},
            "hosts": hosts}
    if portal_ip:
        plan["portal"] = {"enabled": True, "public_ip": portal_ip, "ssh": portal_ip,
                          "labels": {"provider_id": "i-p"}}
    inv = tmp_path / "workshop.yaml"
    inv.write_text(yaml.safe_dump(plan, sort_keys=False))
    job_path_for(inv).write_text("hosts: {}\n")

    kh = known_hosts_path(WS)
    kh.parent.mkdir(parents=True, exist_ok=True)
    key = _host_key(tmp_path)
    kh.write_text("".join(f"{ip} ssh-ed25519 {key}\n" for ip in ("203.0.113.1", "203.0.113.2", "198.51.100.9")))
    keys = workshop_state_dir(WS) / "student-keys"
    keys.mkdir()
    for hid in ("student-01", "student-02"):
        (keys / hid).write_text("private")
        (keys / f"{hid}.pub").write_text("public")
    return inv


def _ok(*ids):
    return [DeprovisionResult(id=i, ok=True, provider_id="i-x", detail="terminating") for i in ids]


def test_full_teardown_removes_every_local_leftover(tmp_path):
    inv = _setup(tmp_path)
    notes = forget_cloud_hosts(WS, inv, _ok("student-01", "student-02", "security-group"), portal_removed=True)
    assert yaml.safe_load(inv.read_text())["hosts"] == []
    assert not workshop_state_dir(WS).exists()
    assert not job_path_for(inv).exists()
    assert any("removed" in n for n in notes)
    # The user's own inventory settings stay.
    assert yaml.safe_load(inv.read_text())["provider"]["region"] == "eu-north-1"


def test_byo_hosts_and_their_keys_are_kept(tmp_path):
    inv = _setup(tmp_path, byo=True)
    forget_cloud_hosts(WS, inv, _ok("student-01", "student-02"), portal_removed=True)
    hosts = yaml.safe_load(inv.read_text())["hosts"]
    assert [h["id"] for h in hosts] == ["lab-box"]
    kh = known_hosts_path(WS).read_text()
    assert "198.51.100.9" in kh and "203.0.113.1" not in kh
    assert job_path_for(inv).exists()  # a host is still in the workshop


def test_a_failed_terminate_keeps_that_host(tmp_path):
    inv = _setup(tmp_path)
    results = _ok("student-01") + [DeprovisionResult(id="student-02", ok=False, error="boom", provider_id="i-2")]
    forget_cloud_hosts(WS, inv, results, portal_removed=True)
    assert [h["id"] for h in yaml.safe_load(inv.read_text())["hosts"]] == ["student-02"]
    keys = workshop_state_dir(WS) / "student-keys"
    assert not (keys / "student-01").exists() and (keys / "student-02").exists()
    kh = known_hosts_path(WS).read_text()
    assert "203.0.113.2" in kh and "203.0.113.1" not in kh
    assert job_path_for(inv).exists()


def test_a_kept_portal_keeps_the_workshop_dir(tmp_path):
    inv = _setup(tmp_path, portal_ip="203.0.113.50")
    forget_cloud_hosts(WS, inv, _ok("student-01", "student-02"), portal_removed=False)
    assert yaml.safe_load(inv.read_text())["hosts"] == []
    assert known_hosts_path(WS).is_file()  # still needed to reach the portal
    assert not (workshop_state_dir(WS) / "student-keys" / "student-01").exists()


def test_no_instance_left_at_all_counts_as_every_host_gone(tmp_path):
    inv = _setup(tmp_path)
    forget_cloud_hosts(WS, inv, [DeprovisionResult(id="*", ok=True, detail="no matching instances")], portal_removed=True)
    assert yaml.safe_load(inv.read_text())["hosts"] == []
    assert not workshop_state_dir(WS).exists()


def test_only_the_selected_hosts_go_with_host_filter(tmp_path):
    inv = _setup(tmp_path)
    forget_cloud_hosts(WS, inv, _ok("student-02"), portal_removed=False)
    assert [h["id"] for h in yaml.safe_load(inv.read_text())["hosts"]] == ["student-01"]
    assert (workshop_state_dir(WS) / "student-keys" / "student-01").exists()


def test_destroy_cloud_removes_the_single_host_state_dir(tmp_path):
    kh = known_hosts_path("rancher-lab")
    kh.parent.mkdir(parents=True, exist_ok=True)
    kh.write_text("x\n")
    assert forget_single_host("rancher-lab")
    assert not kh.parent.exists()
    assert forget_single_host("rancher-lab") == []


def test_fleet_deprovision_cmd_cleans_up(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from rodeo.commands import fleet_cmd

    inv = _setup(tmp_path)
    monkeypatch.setattr(fleet_cmd, "fleet_deprovision", lambda inventory, host_ids=None: _ok("student-01", "student-02"))
    monkeypatch.setattr(fleet_cmd, "deprovision_portal", lambda inventory, path: [])
    result = CliRunner().invoke(fleet_cmd.fleet_cmd, ["deprovision", "--yes", "-f", str(inv)])
    assert result.exit_code == 0, result.output
    assert "local:" in result.output
    assert not workshop_state_dir(WS).exists()


@pytest.mark.parametrize("ok", [True, False])
def test_destroy_cmd_cleans_only_after_success(tmp_path, monkeypatch, ok):
    from click.testing import CliRunner

    from rodeo.commands import destroy_cmd

    kh = known_hosts_path("aws-lab")
    kh.parent.mkdir(parents=True, exist_ok=True)
    kh.write_text("x\n")
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "rodeo-plan.yaml").write_text(yaml.safe_dump({
        "type": "rancher", "name": "aws-lab", "deployment_target": "aws",
        "provider": {"type": "aws", "region": "eu-north-1", "subnet_id": "subnet-1", "instance_type": "m8id.4xlarge"},
    }))
    res = DeprovisionResult(id="primary", ok=ok, provider_id="i-1", detail="terminating", error=None if ok else "boom")
    monkeypatch.setattr(destroy_cmd, "destroy_primary", lambda cfg: [res])
    monkeypatch.setattr(destroy_cmd, "validate_config", lambda cfg: None)
    result = CliRunner().invoke(destroy_cmd.destroy_cmd, ["--cloud", "--yes", "--config-dir", str(lab)])
    assert kh.parent.exists() is (not ok), result.output
