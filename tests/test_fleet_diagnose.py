"""Tests for fleet diagnose (log collect / forensics)."""
from __future__ import annotations

import base64
import io
import json
import tarfile
import textwrap
from pathlib import Path

from click.testing import CliRunner

from rodeo.fleet.diagnose import (
    collect_script,
    extract_collect_b64,
    failed_phases,
    fleet_diagnose,
    host_needs_forensics,
    phase_error_summary,
)
from rodeo.fleet.inventory import load_inventory
from rodeo.fleet.job import job_path_for, new_job, save_job
from rodeo.fleet.ssh_exec import RemoteResult
from rodeo.fleet.status import HostStatusResult


def _workshop(tmp_path: Path) -> Path:
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
    return path


def _b64_tar(files: dict[str, str]) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in files.items():
            data = body.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_collect_script_shape(tmp_path):
    inv = load_inventory(_workshop(tmp_path))
    script = collect_script(inv, lines=200, tmux_session="rodeo-fleet-demo-h1")
    assert "rodeo status --output json" in script
    assert "fleet-up.log" in script
    assert "base64" in script
    assert "tmux capture-pane" in script
    assert "/root/lab" in script
    assert "{{" not in script
    assert "|| { echo" in script


def test_extract_collect_b64(tmp_path):
    b64 = _b64_tar(
        {
            "status.json": '{"name":"lab","phases":{}}',
            "logs/fleet-up.log": "boom line\n",
        }
    )
    dest = tmp_path / "h1"
    names = extract_collect_b64(b64, dest)
    assert (dest / "status.json").is_file()
    assert (dest / "logs" / "fleet-up.log").read_text() == "boom line\n"
    assert any("status.json" in n for n in names)


def test_extract_rejects_path_traversal(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"x"
        info = tarfile.TarInfo(name="../evil")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    dest = tmp_path / "h1"
    names = extract_collect_b64(b64, dest)
    assert names == []
    assert not (tmp_path / "evil").exists()


def test_failed_phases_and_summary():
    report = {
        "phases": {
            "kvm_host": {"completed": True},
            "rancher": {"completed": False, "last_error": "helm timeout"},
        }
    }
    assert failed_phases(report) == ["rancher"]
    assert "helm timeout" in (phase_error_summary(report) or "")


def test_host_needs_forensics():
    ok = HostStatusResult(
        id="h1",
        ok=True,
        error=None,
        report={"phases": {"vms": {"completed": True}}},
    )
    bad = HostStatusResult(
        id="h2",
        ok=True,
        error=None,
        report={"phases": {"vms": {"completed": False, "last_error": "x"}}},
    )
    assert host_needs_forensics(ok, job=None) is False
    assert host_needs_forensics(bad, job=None) is True


def test_fleet_diagnose_writes_artifacts(monkeypatch, tmp_path):
    path = _workshop(tmp_path)
    inv = load_inventory(path)
    report = {
        "name": "lab",
        "vip": "192.168.122.10",
        "vip_reachable": False,
        "vms": [],
        "phases": {
            "rancher": {"completed": False, "last_error": "import failed"},
        },
    }
    payload = _b64_tar(
        {
            "status.json": json.dumps(report),
            "logs/fleet-up.log": "ERROR import failed\n",
            "meta/status.rc": "0\n",
        }
    )

    def fake_run(inventory, host, argv, *, timeout=120.0):
        return RemoteResult(host.id, 0, payload, "")

    monkeypatch.setattr("rodeo.fleet.diagnose.run_remote", fake_run)
    outdir = tmp_path / "out"
    results, out = fleet_diagnose(
        inv,
        [inv.hosts[0]],
        inventory_path=path,
        outdir=outdir,
        concurrency=1,
        lines=100,
    )
    assert out == outdir
    assert results[0].ok is True
    assert results[0].needs_attention is True
    assert results[0].failed_phases == ["rancher"]
    assert (outdir / "h1" / "status.json").is_file()
    assert (outdir / "h1" / "summary.json").is_file()
    assert (outdir / "index.json").is_file()
    assert "import failed" in (outdir / "h1" / "logs" / "fleet-up.log").read_text()


def test_fleet_diagnose_cli_failed_only(monkeypatch, tmp_path):
    path = _workshop(tmp_path)
    job = new_job(
        workshop="demo",
        inventory_path=path,
        concurrency=2,
        host_ids=["h1", "h2"],
    )
    job.set_host("h1", state="failed", last_error="phase rancher: boom", tmux="sess-h1")
    job.set_host("h2", state="ok")
    save_job(job, job_path_for(path))

    report = {
        "name": "lab",
        "phases": {"rancher": {"completed": False, "last_error": "boom"}},
    }
    payload = _b64_tar({"status.json": json.dumps(report), "logs/fleet-up.log": "x\n"})

    def fake_run(inventory, host, argv, *, timeout=120.0):
        assert host.id == "h1"  # failed-only
        return RemoteResult(host.id, 0, payload, "")

    monkeypatch.setattr("rodeo.fleet.diagnose.run_remote", fake_run)

    from rodeo.cli import cli

    out = tmp_path / "diag"
    result = CliRunner().invoke(
        cli,
        [
            "fleet",
            "diagnose",
            "-f",
            str(path),
            "--failed-only",
            "-o",
            str(out),
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["workshop"] == "demo"
    assert len(body["hosts"]) == 1
    assert body["hosts"][0]["id"] == "h1"
    assert body["hosts"][0]["needs_attention"] is True
    assert (out / "h1" / "status.json").is_file()
