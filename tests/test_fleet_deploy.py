"""Tests for fleet deploy start / retry with mocked SSH."""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import textwrap

import pytest
from click.testing import CliRunner

from rodeo.fleet.deploy import (
    HostDeployResult,
    deploy_remote_script,
    fleet_deploy,
    tmux_session_name,
)
from rodeo.config import ConfigError
from rodeo.fleet.inventory import FleetHost, load_inventory, require_deploy_config
from rodeo.install_source import install_url_for_ref
from rodeo.fleet.job import job_path_for, load_job, new_job, save_job
from rodeo.fleet.ssh_exec import RemoteResult, ssh_argv
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


def test_fleet_bootstrap_without_a_ref_leaves_an_existing_install_alone(tmp_path):
    """Default fleet behaviour: only a host missing rodeo gets bootstrapped."""
    inv = load_inventory(_workshop(tmp_path))
    script = deploy_remote_script(inv, tmux_session_name(inv.name, "h1"))
    assert "if ! command -v rodeo >/dev/null 2>&1; then" in script
    assert "--ref" not in script
    assert inv.ref is None


def test_fleet_lab_ref_forces_the_bootstrap(tmp_path):
    """Same bug as the AWS path had: a host bootstrapped before a commit kept
    running the code it was first installed with, so a fleet-wide deploy could
    silently run stale code on every host at once."""
    inv = load_inventory(_workshop(tmp_path, extra_lab="ref: feat/x"))
    assert inv.ref == "feat/x"
    script = deploy_remote_script(inv, tmux_session_name(inv.name, "h1"))
    assert "bash -s -- --ref feat/x" in script
    assert "command -v rodeo >/dev/null 2>&1" not in script


def test_fleet_installer_is_fetched_from_the_same_ref(tmp_path):
    inv = load_inventory(_workshop(tmp_path, extra_lab="ref: v0.15.0"))
    assert inv.install_url == install_url_for_ref("v0.15.0")
    assert inv.install_url_explicit is False


def test_fleet_explicit_install_url_is_kept_verbatim(tmp_path):
    inv = load_inventory(
        _workshop(
            tmp_path,
            extra_lab="install_url: https://mirror.internal/install.sh\n              ref: v0.15.0",
        )
    )
    assert inv.install_url == "https://mirror.internal/install.sh"
    assert inv.install_url_explicit is True
    assert inv.ref == "v0.15.0"


def test_fleet_bad_lab_ref_fails_at_load(tmp_path):
    """Rejected before any host is contacted."""
    with pytest.raises(ConfigError, match="invalid rodeo-cli ref"):
        load_inventory(_workshop(tmp_path, extra_lab="ref: 'main; rm -rf /'"))


def test_fleet_deploy_and_retry_expose_ref():
    from rodeo.commands.fleet_cmd import fleet_deploy_cmd, fleet_retry_cmd

    for cmd in (fleet_deploy_cmd, fleet_retry_cmd):
        assert "ref" in {p.name for p in cmd.params}, f"{cmd.name} must expose --ref"


def test_fleet_ref_flag_overrides_lab_ref_and_repoints_the_installer(tmp_path, monkeypatch):
    """--ref must also move the installer URL. Keeping the resolved default
    would fetch install.sh from main while checking out the ref."""
    from rodeo.cli import cli

    path = _workshop(tmp_path, extra_lab="ref: v0.15.0")
    seen: dict[str, object] = {}

    def _fake_deploy(inventory, hosts, **kwargs):
        seen["inv"] = inventory
        return [], None, tmp_path / "job.yaml"  # the command ignores the job

    monkeypatch.setattr("rodeo.commands.fleet_cmd.fleet_deploy", _fake_deploy)
    result = CliRunner().invoke(cli, ["fleet", "deploy", "-f", str(path), "--ref", "feat/x"])
    assert result.exit_code == 0, result.output
    inv = seen["inv"]
    assert inv.ref == "feat/x"
    assert inv.install_url == install_url_for_ref("feat/x")


def test_fleet_ref_flag_rejects_a_hostile_ref(tmp_path):
    from rodeo.cli import cli

    path = _workshop(tmp_path)
    result = CliRunner().invoke(cli, ["fleet", "deploy", "-f", str(path), "--ref", "a b"])
    assert result.exit_code == 1
    assert "invalid rodeo-cli ref" in result.output


def _workshop_git(tmp_path, name="demo"):
    path = tmp_path / "workshop.yaml"
    path.write_text(
        textwrap.dedent(
            f"""
            name: {name}
            lab:
              dir: /root/lab
              source: git:https://github.com/avaleror/suse-virt-workshop.git
              branch: main
              target: baremetal
            hosts:
              - id: h1
                ssh: 10.0.0.1
            """
        )
    )
    return path


def _workshop_profile(tmp_path, name="demo"):
    path = tmp_path / "workshop.yaml"
    path.write_text(
        textwrap.dedent(
            f"""
            name: {name}
            lab:
              dir: /root/lab
              profile: harvester
              target: instruqt
            hosts:
              - id: h1
                ssh: 10.0.0.1
            """
        )
    )
    return path


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
@pytest.mark.parametrize("build_inventory", [_workshop_git, _workshop_profile])
def test_deploy_remote_script_is_valid_bash(tmp_path, build_inventory):
    """Regression guard: deploy_remote_script hand-assembles bootstrap + sync
    + tmux-start by nesting shlex.quote()'d fragments inside each other. A
    quoting bug here silently breaks every remote deploy — this can't be
    caught by substring assertions, only by actually parsing the result."""
    path = build_inventory(tmp_path)
    inv = load_inventory(path)
    require_deploy_config(inv)
    session = tmux_session_name(inv.name, "h1")
    script = deploy_remote_script(inv, session)

    proc = subprocess.run(["bash", "-n", "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, f"invalid bash syntax: {proc.stderr}\n\nscript:\n{script}"


def test_deploy_remote_script_survives_full_ssh_quoting_roundtrip(tmp_path):
    """The script above is itself wrapped in shlex.quote() twice more (once
    for `bash -lc <script>`, once for the outer ssh argv) before it ever
    reaches a shell — verify the fully-wrapped command still parses back to
    exactly the same tokens, i.e. nothing was lost or corrupted in transit."""
    path = _workshop(tmp_path)
    inv = load_inventory(path)
    session = tmux_session_name(inv.name, "h1")
    script = deploy_remote_script(inv, session)

    inner_argv = ["bash", "-lc", script]
    remote_command = " ".join(shlex.quote(a) for a in inner_argv)
    assert shlex.split(remote_command) == inner_argv

    full_argv = ssh_argv(inv, FleetHost(id="h1", ssh="10.0.0.1"), remote_command)
    assert full_argv[-1] == remote_command


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


def test_fleet_deploy_skips_when_apply_incomplete(monkeypatch, tmp_path):
    """apply is no_cache and never completed — must not block fleet skip/ok."""
    path = _workshop(tmp_path)
    inv = load_inventory(path)

    def fake_run(inventory, host, argv, *, timeout=120.0):
        remote = argv[-1] if argv else ""
        if "status --output json" in remote:
            body = {
                "name": "lab",
                "phases": {
                    "kvm_host": {"completed": True},
                    "vms": {"completed": True},
                    "pxe_server": {"completed": True},
                    "cluster": {"completed": True},
                    "rancher": {"completed": True},
                    "apply": {"completed": False, "no_cache": True},
                    "finalise": {"completed": True},
                },
            }
            return RemoteResult(host.id, 0, json.dumps(body), "")
        raise AssertionError("deploy should not start when cacheable phases done")

    monkeypatch.setattr("rodeo.fleet.deploy.run_remote", fake_run)
    results, job, _ = fleet_deploy(
        inv, [inv.hosts[0]], inventory_path=path, concurrency=1, force=False
    )
    assert results[0].state == "skipped"
    assert job.hosts["h1"].state == "ok"


def test_fleet_deploy_skips_legacy_status_without_no_cache_flag(monkeypatch, tmp_path):
    """Older remotes omit no_cache; apply by name must still be ignored."""
    path = _workshop(tmp_path)
    inv = load_inventory(path)

    def fake_run(inventory, host, argv, *, timeout=120.0):
        remote = argv[-1] if argv else ""
        if "status --output json" in remote:
            body = {
                "name": "lab",
                "phases": {
                    "kvm_host": {"completed": True},
                    "vms": {"completed": True},
                    "rancher": {"completed": True},
                    "apply": {"completed": False},
                    "finalise": {"completed": True},
                },
            }
            return RemoteResult(host.id, 0, json.dumps(body), "")
        raise AssertionError("deploy should not start")

    monkeypatch.setattr("rodeo.fleet.deploy.run_remote", fake_run)
    results, _, _ = fleet_deploy(
        inv, [inv.hosts[0]], inventory_path=path, concurrency=1, force=False
    )
    assert results[0].state == "skipped"


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
