"""fleet deploy keeps rodeo up's real exit code, and status/job trust it."""
from __future__ import annotations

import json
import subprocess
import textwrap

from rodeo.fleet import status as fstatus
from rodeo.fleet.deploy import apply_status_to_job, start_up_script
from rodeo.fleet.inventory import load_inventory
from rodeo.fleet.job import new_job
from rodeo.fleet.ssh_exec import RemoteResult
from rodeo.fleet.status import HostStatusResult, _split_marker

DONE = {"kvm_host": {"completed": True}, "finalise": {"completed": True},
        "custom_scripts": {"completed": False, "no_cache": True}}


def _inv(tmp_path):
    p = tmp_path / "workshop.yaml"
    p.write_text(textwrap.dedent("""
        name: demo
        lab: {dir: /root/lab, profile: virt-workshop}
        hosts: [{id: h1, ssh: 10.0.0.1}]
    """))
    return load_inventory(p)


def test_up_script_keeps_rodeo_ups_exit_code_not_tees(tmp_path):
    script = start_up_script(_inv(tmp_path), "rodeo-fleet-demo-h1")
    assert "set -o pipefail" in script
    assert "fleet-up.exit" in script and "rc=$?; echo $rc >" in script
    # The real thing: a failing command piped to tee must record its own code.
    inner = 'set -o pipefail; (exit 3) 2>&1 | tee -a /dev/null; rc=$?; echo $rc'
    assert subprocess.run(["bash", "-c", inner], capture_output=True, text=True).stdout.strip() == "3"


def test_marker_parsing():
    assert _split_marker('{"a": 1}\nRODEO_FLEET_UP:1:0\n') == ('{"a": 1}\n', 1, False)
    assert _split_marker('{"a": 1}\nRODEO_FLEET_UP::1\n') == ('{"a": 1}\n', None, True)
    assert _split_marker('{"a": 1}') == ('{"a": 1}', None, None)


def test_status_reads_the_exit_code(tmp_path, monkeypatch):
    inv = _inv(tmp_path)
    out = json.dumps({"name": "lab", "phases": DONE}) + "\nRODEO_FLEET_UP:1:0\n"
    monkeypatch.setattr(fstatus, "run_remote", lambda *a, **k: RemoteResult("h1", 0, out, ""))
    r = fstatus.fleet_status(inv, inv.hosts)[0]
    assert r.ok and r.report["fleet_up_exit"] == 1 and r.report["fleet_up_running"] is False


def _job(tmp_path):
    return new_job(workshop="demo", inventory_path=tmp_path / "w.yaml", concurrency=1, host_ids=["h1"])


def _state(tmp_path, **extra):
    job = _job(tmp_path)
    report = {"phases": DONE, **extra}
    apply_status_to_job(job, [HostStatusResult("h1", True, None, report)])
    return job.hosts["h1"]


def test_failed_rodeo_up_is_failed_even_when_phases_look_done(tmp_path):
    rec = _state(tmp_path, fleet_up_exit=1)
    assert rec.state == "failed" and "exited 1" in rec.last_error


def test_running_deploy_is_not_ok_yet(tmp_path):
    assert _state(tmp_path, fleet_up_running=True).state == "running"


def test_success_and_old_hosts_keep_todays_behaviour(tmp_path):
    assert _state(tmp_path, fleet_up_exit=0).state == "ok"
    assert _state(tmp_path).state == "ok"  # no exit file: deploys from before this change


def test_player_fleets_make_the_student_vm_key_before_rodeo_up(tmp_path, monkeypatch):
    """suse-edge bakes the student key into edge3/edge4 during rodeo up, so it
    must exist first. Root's key is never involved."""
    from rodeo.fleet import deploy, inventory
    from rodeo.player.profile import parse

    inv = _inv(tmp_path)
    monkeypatch.setattr(inventory, "runs_player", lambda i: True)
    prof = parse("x", {"title": "X", "uis": [{"key": "r", "upstream": "192.168.122.9:30002",
                                              "tab_port": 30002}],
                       "student_vm_access": ["192.168.122.20"]})
    monkeypatch.setattr("rodeo.player.profile.load", lambda name: prof)
    script = start_up_script(inv, "rodeo-fleet-demo-h1")
    assert "ssh-keygen" in script and "/var/lib/rodeo-player/student-vm-key" in script
    assert script.index("ssh-keygen") < script.index("rodeo up")
    assert "/root/.ssh" not in deploy.student_key_script(inv)

    monkeypatch.setattr(inventory, "runs_player", lambda i: False)
    assert deploy.student_key_script(inv) == ""
