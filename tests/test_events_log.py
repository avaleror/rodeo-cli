"""WP6: per-lab deploy events in <lab>/.rodeo/events.jsonl (built-in event sink)."""
from __future__ import annotations

import json
import os
import stat

import pytest

from rodeo import events_log, hooks, labfs, plugins
from rodeo.config import ConfigError
from rodeo.engine.runner import DeployRunner, LogLine


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(hooks, "_sinks", [])
    monkeypatch.setattr(plugins, "_loaded", True)
    monkeypatch.setattr(events_log, "_warned", set())
    from rodeo import inventory as inv_mod

    real_build = inv_mod.build_inventory
    monkeypatch.setattr(inv_mod, "build_inventory",
                        lambda cfg: {} if cfg.get("type") == "fake" else real_build(cfg))


@pytest.fixture
def lab(tmp_path):
    path = tmp_path / "lab"
    path.mkdir()
    return path


def _lines(lab):
    return [json.loads(x) for x in (lab / ".rodeo" / "events.jsonl").read_text().splitlines()]


def test_fake_run_writes_one_line_per_event(fake_profile, fake_cfg, lab, tmp_path):
    def run_phase(phase, runner, vars_file):
        runner._last_rc = 2 if phase == "beta" else 0
        yield LogLine(f"working on {phase}")

    fake_profile.run_phase = run_phase
    cfg = {**fake_cfg, "plan_dir": str(lab)}
    events = list(DeployRunner(cfg, tmp_path).run())
    lines = _lines(lab)
    assert len(lines) == len(events)
    assert [(r["kind"], r["phase"]) for r in lines] == [
        ("PhaseStarted", "alpha"), ("LogLine", "alpha"), ("PhaseDone", "alpha"),
        ("PhaseStarted", "beta"), ("LogLine", "beta"), ("PhaseFailed", "beta"),
    ]
    assert lines[1]["text"] == "working on alpha"
    assert lines[-1]["rc"] == 2
    assert all(r["lab"] == os.path.realpath(lab) and r["v"] == 0 and "ts" in r for r in lines)
    mode = stat.S_IMODE(os.stat(lab / ".rodeo" / "events.jsonl").st_mode)
    assert mode == 0o600


def test_secret_in_a_log_line_is_masked(fake_profile, fake_cfg, lab, tmp_path):
    def run_phase(phase, runner, vars_file):
        runner._last_rc = 0
        yield LogLine(f"admin password is {fake_cfg['credentials']['harvester_os_password']}")
        yield LogLine("git clone https://deploy:gh_tok_99999@git.example/x")

    fake_profile.run_phase = run_phase
    list(DeployRunner({**fake_cfg, "plan_dir": str(lab)}, tmp_path).run())
    text = (lab / ".rodeo" / "events.jsonl").read_text()
    assert "Secret123" not in text and "gh_tok_99999" not in text
    assert "admin password is ***" in text


def test_failing_write_never_fails_the_deploy(fake_profile, fake_cfg, lab, tmp_path, caplog):
    (lab / ".rodeo").write_text("not a directory")
    events = list(DeployRunner({**fake_cfg, "plan_dir": str(lab)}, tmp_path).run())
    assert type(events[-1]).__name__ == "DeployComplete"
    assert caplog.text.count("events: cannot write") == 1  # once per lab, not per line


def test_events_file_symlink_is_not_followed(fake_profile, fake_cfg, lab, tmp_path):
    target = tmp_path / "victim"
    target.write_text("keep\n")
    (lab / ".rodeo").mkdir()
    (lab / ".rodeo" / "events.jsonl").symlink_to(target)
    events = list(DeployRunner({**fake_cfg, "plan_dir": str(lab)}, tmp_path).run())
    assert type(events[-1]).__name__ == "DeployComplete"
    assert target.read_text() == "keep\n"


def test_dot_rodeo_symlink_is_not_followed(lab, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (lab / ".rodeo").symlink_to(elsewhere)
    with pytest.raises(ConfigError):
        labfs.append_lab_file(lab, ".rodeo/events.jsonl", "x\n")
    assert list(elsewhere.iterdir()) == []


def test_hard_link_is_refused(lab, tmp_path):
    target = tmp_path / "victim"
    target.write_text("keep\n")
    (lab / ".rodeo").mkdir()
    os.link(target, lab / ".rodeo" / "events.jsonl")
    with pytest.raises(ConfigError):
        labfs.append_lab_file(lab, ".rodeo/events.jsonl", "x\n")
    assert target.read_text() == "keep\n"


def test_rotation_keeps_one_old_file(lab):
    for i in range(3):
        labfs.append_lab_file(lab, ".rodeo/events.jsonl", f"{i}" * 60 + "\n", max_bytes=100)
    d = lab / ".rodeo"
    assert sorted(p.name for p in d.iterdir()) == ["events.jsonl", "events.jsonl.1"]
    assert (d / "events.jsonl").read_text().startswith("2")
    assert (d / "events.jsonl.1").read_text().startswith("1")


def test_rotation_threshold_is_10_mb():
    assert events_log.MAX_BYTES == 10 * 1024 * 1024


def test_existing_file_is_tightened_to_0600(lab):
    (lab / ".rodeo").mkdir()
    f = lab / ".rodeo" / "events.jsonl"
    f.write_text("")
    f.chmod(0o644)
    labfs.append_lab_file(lab, ".rodeo/events.jsonl", "x\n")
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_record_without_lab_is_skipped(tmp_path):
    events_log.write_event({"lab": None, "kind": "LogLine", "text": "x"})
    assert not any(tmp_path.rglob("events.jsonl"))
