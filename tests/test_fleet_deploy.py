"""Tests for fleet deploy start / retry with mocked SSH."""
from __future__ import annotations

import json
import textwrap

from click.testing import CliRunner

from rodeo.fleet.deploy import (
    HostDeployResult,
    deploy_remote_script,
    fleet_deploy,
    tmux_session_name,
)
from rodeo.fleet.inventory import load_inventory, require_deploy_config
from rodeo.fleet.job import job_path_for, load_job, new_job, save_job
from rodeo.fleet.ssh_exec import RemoteResult
from rodeo.fleet.sync import normalize_git_url, sync_script


def _workshop(tmp_path, extra_lab: str = ""):
    path = tmp_path / "workshop.yaml"
    path.write_text(
        textwrap.dedent(
            f"""
            name: demo
            lab:
              dir: /root/lab
              source: git:https://github.com/avaleror/suse-virt-workshop.git
              target: baremetal
              concurrency: 2
              {extra_lab}
            hosts:
              - id: h1
                ssh: 10.0.0.1
                public_ip: 203.0.113.1
              - id: h2
                ssh: 10.0.0.2
                public_ip: 203.0.113.2
            """
        )
    )
    return path


def test_normalize_git_url():
    assert normalize_git_url("git:https://example.com/r.git") == "https://example.com/r.git"
    assert normalize_git_url("https://example.com/r.git") == "https://example.com/r.git"


def test_sync_and_deploy_scripts(tmp_path):
    path = _workshop(tmp_path)
    inv = load_inventory(path)
    require_deploy_config(inv)
    sync = sync_script(inv)
    assert "git clone" in sync
    assert "suse-virt-workshop" in sync
    session = tmux_session_name(inv.name, "h1")
    script = deploy_remote_script(inv, session)
    assert "install.sh" in script or "command -v rodeo" in script
    assert "tmux new-session" in script
    assert "rodeo up --yes --no-tmux" in script


def test_fleet_deploy_writes_job(monkeypatch, tmp_path):
    path = _workshop(tmp_path)
    inv = load_inventory(path)

    calls = {"n": 0}

    def fake_run(inventory, host, argv, *, timeout=120.0):
        calls["n"] += 1
        # status skip probe (first call pattern) or deploy
        remote = argv[-1] if argv else ""
        if "status --output json" in remote:
            return RemoteResult(host.id, 1, "", "no lab yet")
        return RemoteResult(host.id, 0, f"STARTED:rodeo-fleet-demo-{host.id}\n", "")

    monkeypatch.setattr("rodeo.fleet.deploy.run_remote", fake_run)
    results, job, job_path = fleet_deploy(
        inv, inv.hosts, inventory_path=path, concurrency=2, force=True
    )
    assert job_path == job_path_for(path)
    assert all(r.ok and r.state == "running" for r in results)
    loaded = load_job(job_path)
    assert loaded.hosts["h1"].state == "running"
    assert loaded.hosts["h1"].tmux


def test_fleet_deploy_skips_complete(monkeypatch, tmp_path):
    path = _workshop(tmp_path)
    inv = load_inventory(path)

    def fake_run(inventory, host, argv, *, timeout=120.0):
        remote = argv[-1] if argv else ""
        if "status --output json" in remote:
            body = {
                "name": "lab",
                "vip": "192.168.122.10",
                "vip_reachable": True,
                "vms": [],
                "phases": {
                    "kvm_host": {"completed": True},
                    "vms": {"completed": True},
                },
            }
            return RemoteResult(host.id, 0, json.dumps(body), "")
        raise AssertionError("deploy should not start when complete")

    monkeypatch.setattr("rodeo.fleet.deploy.run_remote", fake_run)
    results, job, _ = fleet_deploy(
        inv, [inv.hosts[0]], inventory_path=path, concurrency=1, force=False
    )
    assert results[0].state == "skipped"
    assert job.hosts["h1"].state == "ok"


def test_fleet_deploy_cli(monkeypatch, tmp_path):
    path = _workshop(tmp_path)

    def fake_deploy(*args, **kwargs):
        return (
            [
                HostDeployResult("h1", True, "running", None, "sess", "STARTED"),
                HostDeployResult("h2", True, "running", None, "sess2", "STARTED"),
            ],
            new_job(
                workshop="demo",
                inventory_path=path,
                concurrency=2,
                host_ids=["h1", "h2"],
            ),
            job_path_for(path),
        )

    monkeypatch.setattr("rodeo.commands.fleet_cmd.fleet_deploy", fake_deploy)
    from rodeo.cli import cli

    result = CliRunner().invoke(
        cli, ["fleet", "deploy", "-f", str(path), "--output", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["workshop"] == "demo"
    assert len(payload["hosts"]) == 2


def test_fleet_retry_failed_only(monkeypatch, tmp_path):
    path = _workshop(tmp_path)
    job = new_job(
        workshop="demo",
        inventory_path=path,
        concurrency=2,
        host_ids=["h1", "h2"],
    )
    job.set_host("h1", state="failed", last_error="x")
    job.set_host("h2", state="ok")
    save_job(job, job_path_for(path))

    retried: list[str] = []

    def fake_refresh(*a, **k):
        return load_job(job_path_for(path))

    def fake_deploy(inventory, hosts, **kwargs):
        retried.extend(h.id for h in hosts)
        return (
            [HostDeployResult(h.id, True, "running", None, "s", "ok") for h in hosts],
            kwargs.get("merge_job") or job,
            job_path_for(path),
        )

    monkeypatch.setattr("rodeo.commands.fleet_cmd.refresh_job_from_status", fake_refresh)
    monkeypatch.setattr("rodeo.commands.fleet_cmd.fleet_deploy", fake_deploy)
    monkeypatch.setattr(
        "rodeo.commands.fleet_cmd.load_job",
        lambda p: load_job(p),
    )

    from rodeo.cli import cli

    result = CliRunner().invoke(cli, ["fleet", "retry", "-f", str(path), "--output", "json"])
    assert result.exit_code == 0, result.output
    assert retried == ["h1"]


def test_fleet_access_cli(tmp_path):
    path = _workshop(tmp_path)
    from rodeo.cli import cli

    result = CliRunner().invoke(
        cli, ["fleet", "access", "-f", str(path), "--output", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["hosts"][0]["harvester_url"] == "https://203.0.113.1:8443"


def test_require_deploy_config_missing(tmp_path):
    path = tmp_path / "workshop.yaml"
    path.write_text(
        textwrap.dedent(
            """
            lab:
              dir: /root/lab
            hosts:
              - id: a
                ssh: 10.0.0.1
            """
        )
    )
    inv = load_inventory(path)
    import pytest
    from rodeo.config import ConfigError

    with pytest.raises(ConfigError, match="lab.source"):
        require_deploy_config(inv)
