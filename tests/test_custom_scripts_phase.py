"""stream_custom_scripts — the custom/scripts/ phase.

Documented since the config-dir feature shipped ("numbered scripts run in
order for custom bootstrap or post-deploy steps") but never actually
executed until this phase: config_dir.py discovered them into
_config_dir['custom_scripts'] and nothing consumed it.
"""
from __future__ import annotations

import stat
import subprocess

from rodeo.engine.runner import DeployRunner, LogLine


def _drain(gen):
    return list(gen)


def _make_script(path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_runs_scripts_in_sorted_order_with_env(tmp_path, monkeypatch):
    scripts_dir = tmp_path / "custom" / "scripts"
    scripts_dir.mkdir(parents=True)
    order_file = tmp_path / "order.txt"
    _make_script(scripts_dir / "20-second.sh", f'echo second >> "{order_file}"')
    _make_script(scripts_dir / "10-first.sh", f'echo first >> "{order_file}"')

    cfg = {"type": "suse-virt", "name": "t", "config_dir": str(tmp_path)}
    runner = DeployRunner(cfg, tmp_path)
    events = _drain(runner.stream_custom_scripts())

    assert order_file.read_text().splitlines() == ["first", "second"]
    assert runner._last_rc == 0
    assert any("10-first.sh" in e.line for e in events if isinstance(e, LogLine))
    assert any("✓" in e.line and "10-first.sh" in e.line for e in events if isinstance(e, LogLine))


def test_script_gets_identifying_env_vars(tmp_path):
    scripts_dir = tmp_path / "custom" / "scripts"
    scripts_dir.mkdir(parents=True)
    out = tmp_path / "env.txt"
    _make_script(
        scripts_dir / "10-env.sh",
        f'echo "$RODEO_PLAN_NAME|$RODEO_LAB_DIR|$RODEO_CONFIG_DIR" > "{out}"',
    )

    cfg = {"type": "suse-virt", "name": "demo-plan", "config_dir": str(tmp_path)}
    runner = DeployRunner(cfg, tmp_path)
    _drain(runner.stream_custom_scripts())

    plan_name, lab_dir, config_dir = out.read_text().strip().split("|")
    assert plan_name == "demo-plan"
    assert lab_dir == str(tmp_path)
    assert config_dir == str(tmp_path)


def test_sets_kubeconfig_when_present(tmp_path, monkeypatch):
    scripts_dir = tmp_path / "custom" / "scripts"
    scripts_dir.mkdir(parents=True)
    out = tmp_path / "kc.txt"
    _make_script(scripts_dir / "10-kc.sh", f'echo "$KUBECONFIG" > "{out}"')

    fake_kubeconfig = tmp_path / "harvester-kubeconfig"
    fake_kubeconfig.write_text("fake")
    monkeypatch.setattr("rodeo.paths.harvester_kubeconfig_path", lambda: fake_kubeconfig)

    cfg = {"type": "suse-virt", "name": "t", "config_dir": str(tmp_path)}
    runner = DeployRunner(cfg, tmp_path)
    _drain(runner.stream_custom_scripts())

    assert out.read_text().strip() == str(fake_kubeconfig)


def test_failing_script_does_not_stop_the_rest_but_fails_the_phase(tmp_path):
    scripts_dir = tmp_path / "custom" / "scripts"
    scripts_dir.mkdir(parents=True)
    marker = tmp_path / "ran-second.txt"
    _make_script(scripts_dir / "10-fails.sh", "exit 3")
    _make_script(scripts_dir / "20-runs-anyway.sh", f'touch "{marker}"')

    cfg = {"type": "suse-virt", "name": "t", "config_dir": str(tmp_path)}
    runner = DeployRunner(cfg, tmp_path)
    events = _drain(runner.stream_custom_scripts())

    assert marker.exists()  # second script still ran
    assert runner._last_rc == 1  # but the phase as a whole is marked failed
    assert any("✗" in e.line and "10-fails.sh" in e.line for e in events if isinstance(e, LogLine))


def test_noop_without_config_dir():
    cfg = {"type": "suse-virt", "name": "t"}
    runner = DeployRunner(cfg, None)
    events = _drain(runner.stream_custom_scripts())
    assert events == []
    assert runner._last_rc == 0


def test_noop_without_scripts_dir(tmp_path):
    cfg = {"type": "suse-virt", "name": "t", "config_dir": str(tmp_path)}
    runner = DeployRunner(cfg, tmp_path)
    events = _drain(runner.stream_custom_scripts())
    assert events == []
    assert runner._last_rc == 0


def test_non_executable_files_are_skipped(tmp_path):
    scripts_dir = tmp_path / "custom" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "10-not-executable.sh").write_text("#!/bin/sh\nexit 0\n")  # no chmod +x

    called = {"n": 0}

    def fake_run(*a, **k):
        called["n"] += 1
        return subprocess.CompletedProcess(a, 0, stdout="", stderr="")

    import subprocess as subprocess_mod

    orig = subprocess_mod.run
    subprocess_mod.run = fake_run
    try:
        cfg = {"type": "suse-virt", "name": "t", "config_dir": str(tmp_path)}
        runner = DeployRunner(cfg, tmp_path)
        _drain(runner.stream_custom_scripts())
    finally:
        subprocess_mod.run = orig

    assert called["n"] == 0
