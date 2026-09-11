"""LabInABoxRunner — event contract for the lab-in-a-box deploy engine.

The subprocess layer (ssh/scp/setup_lab.py) is faked; these tests pin the
event sequence, the remote invocation contract (setup_lab.py --keep --debug,
PYTHONUNBUFFERED, remote lab.json path), failure summaries from the upstream
LAB SUMMARY block, cancellation, and the destroy helper.
"""
from __future__ import annotations

import json

import pytest

import rodeo.engine.labinabox_runner as liab_mod
from rodeo.engine.events import (
    DeployComplete,
    LogLine,
    PhaseDone,
    PhaseFailed,
    PhaseSkipped,
    PhaseStarted,
)
from rodeo.engine.labinabox_runner import LabInABoxRunner, destroy_remote_lab


def _cfg(**overlay) -> dict:
    lab = {"automation_host": "root@auto.lab", "iso_image": "leap.qcow2"}
    lab.update(overlay)
    return {
        "name": "liab-test",
        "type": "rancher",
        "engine": "lab-in-a-box",
        "lab_in_a_box": lab,
    }


class _FakeStream:
    """stream_command stand-in: records argv, scripts rc/output by substring."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.rcs: dict[str, int] = {}       # substring of argv -> rc
        self.lines: dict[str, list[str]] = {}  # substring of argv -> output

    def __call__(self, cmd, log_file, env=None, on_proc=None):
        self.calls.append(cmd)
        joined = " ".join(cmd)
        for key, lines in self.lines.items():
            if key in joined:
                for line in lines:
                    yield LogLine(line)
        for key, rc in self.rcs.items():
            if key in joined:
                return rc
        return 0


@pytest.fixture()
def fake_stream(monkeypatch):
    fake = _FakeStream()
    monkeypatch.setattr(liab_mod, "stream_command", fake)
    monkeypatch.setattr(
        liab_mod, "build_lab_json",
        lambda cfg: ({"nodes": {}, "common": {"lab_name": cfg["name"]}}, ["a warning"]),
    )
    return fake


def _events(runner):
    return list(runner.run())


def _of_type(events, typ):
    return [e for e in events if isinstance(e, typ)]


def test_happy_path_event_sequence(fake_stream):
    runner = LabInABoxRunner(cfg=_cfg())
    events = _events(runner)

    assert [e.phase for e in _of_type(events, PhaseStarted)] == ["render", "deploy"]
    assert [e.phase for e in _of_type(events, PhaseDone)] == ["render", "deploy"]
    assert _of_type(events, DeployComplete)
    assert not _of_type(events, PhaseFailed)
    # The render warning from build_lab_json surfaces as a log line.
    assert any("a warning" in e.line for e in _of_type(events, LogLine))


def test_remote_invocation_contract(fake_stream):
    runner = LabInABoxRunner(cfg=_cfg())
    _events(runner)

    mkdir, scp, deploy = fake_stream.calls
    # Paths are relative to the SSH user's home on the automation VM
    # (~/rodeo-labs/<plan>) so any automation_host user works, not just root.
    assert mkdir[0] == "ssh" and mkdir[-1] == "mkdir -p rodeo-labs/liab-test"
    assert scp[0] == "scp"
    assert scp[-1] == "root@auto.lab:rodeo-labs/liab-test/lab.json"
    assert deploy[0] == "ssh" and "root@auto.lab" in deploy
    remote = deploy[-1]
    # PYTHONUNBUFFERED beats setup_lab.py's block-buffered stdout off-TTY;
    # --keep keeps the run incremental; --debug streams command output live.
    assert remote == (
        "PYTHONUNBUFFERED=1 setup_lab.py --keep --debug "
        "rodeo-labs/liab-test/lab.json"
    )


def test_local_lab_json_written_with_tight_mode(fake_stream, tmp_path):
    runner = LabInABoxRunner(cfg=_cfg())
    _events(runner)
    local = runner._local_lab_json
    assert json.loads(local.read_text())["common"]["lab_name"] == "liab-test"
    assert (local.stat().st_mode & 0o777) == 0o600


def test_force_requests_upstream_rebuild(fake_stream):
    runner = LabInABoxRunner(cfg=_cfg(), force=True)
    _events(runner)
    remote = fake_stream.calls[-1][-1]
    assert "--keep" not in remote  # upstream default = destroy and recreate


def test_overlay_can_disable_keep_and_debug(fake_stream):
    runner = LabInABoxRunner(cfg=_cfg(keep=False, debug=False))
    _events(runner)
    remote = fake_stream.calls[-1][-1]
    assert "--keep" not in remote
    assert "--debug" not in remote


def test_custom_remote_dir_and_identity(fake_stream):
    runner = LabInABoxRunner(
        cfg=_cfg(remote_dir="/srv/labs/x/", identity_file="/root/.ssh/lab")
    )
    _events(runner)
    mkdir, scp, deploy = fake_stream.calls
    assert mkdir[1] == "-i" and mkdir[2] == "/root/.ssh/lab"
    assert deploy[-1].endswith(" /srv/labs/x/lab.json")


def test_deploy_failure_stops_and_summarizes(fake_stream):
    fake_stream.rcs["setup_lab.py"] = 1
    fake_stream.lines["setup_lab.py"] = [
        "  LAB SUMMARY",
        "  Nodes (1):",
        "    FAILED   rancher.rodeo.lab",
        "✗ Lab setup finished WITH FAILURES — see above.",
    ]
    runner = LabInABoxRunner(cfg=_cfg())
    events = _events(runner)

    failed = _of_type(events, PhaseFailed)
    assert [e.phase for e in failed] == ["deploy"]
    assert failed[0].rc == 1
    assert "FAILED   rancher.rodeo.lab" in failed[0].message
    assert not _of_type(events, DeployComplete)


def test_render_failure_stops_before_deploy(fake_stream):
    fake_stream.rcs["mkdir -p"] = 255  # ssh unreachable
    runner = LabInABoxRunner(cfg=_cfg())
    events = _events(runner)

    assert [e.phase for e in _of_type(events, PhaseFailed)] == ["render"]
    assert len(fake_stream.calls) == 1  # no scp, no setup_lab.py


def test_from_phase_skips_render(fake_stream):
    runner = LabInABoxRunner(cfg=_cfg(), from_phase="deploy")
    events = _events(runner)

    skipped = _of_type(events, PhaseSkipped)
    assert [(e.phase, e.reason) for e in skipped] == [("render", "before_start")]
    assert [e.phase for e in _of_type(events, PhaseStarted)] == ["deploy"]


def test_cancellation_before_phase(fake_stream):
    runner = LabInABoxRunner(cfg=_cfg())
    runner.stop.set()
    events = _events(runner)

    failed = _of_type(events, PhaseFailed)
    assert failed and failed[0].rc == 130
    assert not fake_stream.calls


def test_destroy_remote_lab_surfaces_warnings():
    calls = {}

    class _Result:
        returncode = 0
        stdout = "Destroying VM rancher.rodeo.lab\n"
        stderr = "WARNING: destroy failed for 'rancher.rodeo.lab' (continuing)\n"

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return _Result()

    rc, lines = destroy_remote_lab(_cfg(), run=fake_run)
    assert rc == 0  # destroy_lab.py always exits 0 — output is the signal
    assert calls["cmd"][0] == "ssh"
    assert calls["cmd"][-1] == "destroy_lab.py rodeo-labs/liab-test/lab.json"
    assert any("WARNING" in line for line in lines)
