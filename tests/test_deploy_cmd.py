"""rodeo deploy — plain-mode output must not choke on raw tool output."""
from __future__ import annotations

from rodeo.commands import deploy as deploy_mod
from rodeo.engine.runner import DeployRunner, LogLine


def test_deploy_plain_survives_bracketed_tool_output(monkeypatch, tmp_path, capsys):
    """Regression: hauler's own log format ("adding file [/tmp/foo] to the store
    as [...]") crashed the whole deploy — Rich's console.print() interprets
    "[...]" as markup, and a bracket value starting with "/" parses as an
    (unmatched) closing tag, raising rich.errors.MarkupError and killing the
    process mid-run instead of just printing the line."""

    lines = [
        "adding file [/tmp/leap-micro-selfinstall.iso] to the store",
        "successfully added file [hauler/leap-micro-selfinstall.iso:latest]",
    ]

    def fake_run(self):
        for line in lines:
            yield LogLine(line)

    monkeypatch.setattr(DeployRunner, "run", fake_run)

    code = deploy_mod._deploy_plain(
        cfg={"name": "test"}, root=tmp_path, from_phase=None, install_collections=False,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "/tmp/leap-micro-selfinstall.iso" in out


def test_deploy_plain_interrupt_stops_the_running_step(monkeypatch, tmp_path):
    """Ctrl-C (SIGINT) reaches only rodeo itself: the step runs in its own session, so it is stopped explicitly."""
    import sys

    runners = []

    def fake_run(self):
        runners.append(self)
        yield from self._stream_subprocess(
            [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(60)"])

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(DeployRunner, "run", fake_run)
    monkeypatch.setattr(deploy_mod.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(deploy_mod, "_print_events",
                        lambda runner, status: next(iter(runner.run())) and interrupt())

    code = deploy_mod._deploy_plain(cfg={"name": "test"}, root=tmp_path, from_phase=None, install_collections=False)
    assert code == 130
    proc = runners[0]._proc
    assert proc.wait(timeout=10) != 0 and runners[0].stop.is_set()


def _stub_deploy_check(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(deploy_mod, "is_root", lambda: True)
    monkeypatch.setattr("rodeo.hostos.kvm_host_problem", lambda: None)
    monkeypatch.setattr("rodeo.preflight.detect_host", lambda: {})
    monkeypatch.setattr(deploy_mod, "find_ansible_root", lambda cfg: tmp_path)
    (tmp_path / "ansible").mkdir()
    (tmp_path / "ansible" / "playbook.yml").write_text("[]\n")

    def fake_preflight(cfg, root, phases_to_run=None):
        seen["cfg"] = cfg
        return True

    monkeypatch.setattr(deploy_mod, "run_preflight", fake_preflight)
    return seen


def test_deploy_check_resolves_keys_only(monkeypatch, tmp_path):
    """deploy --check (MCP rodeo_preflight) passes with ??key credentials, resolves
    them, and neither reads ??env: nor runs a trusted ??cmd:."""
    from click.testing import CliRunner

    from rodeo import config

    lab = tmp_path / "lab"
    lab.mkdir()
    marker = tmp_path / "ran"
    spec = f"??cmd:touch {marker} && echo ok"
    (lab / "rodeo-plan.yaml").write_text(
        "credentials:\n"
        "  harvester_os_password: ??harvester_os_password\n"
        "  rancher_admin_password: ??env:RODEO_TEST_PW\n"
        f'registry:\n  token: "{spec}"\n'
    )
    secrets = tmp_path / ".rodeo" / "secrets.yaml"
    secrets.parent.mkdir(parents=True, exist_ok=True)
    secrets.write_text("harvester_os_password: FromSecrets1\n")
    monkeypatch.setenv("RODEO_TEST_PW", "FromEnv12345")
    config.trust_lab(lab)
    seen = _stub_deploy_check(monkeypatch, tmp_path)

    result = CliRunner().invoke(
        deploy_mod.deploy_cmd, ["--config", str(lab / "rodeo-plan.yaml"), "--check"]
    )
    assert result.exit_code == 0, result.output
    cfg = seen["cfg"]
    assert cfg["credentials"]["harvester_os_password"] == "FromSecrets1"
    assert cfg["credentials"]["rancher_admin_password"] == "??env:RODEO_TEST_PW"
    assert cfg["registry"]["token"] == spec
    assert not marker.exists()


def test_deploy_check_refuses_untrusted_lab(monkeypatch, tmp_path):
    from click.testing import CliRunner

    lab = tmp_path / "lab"
    lab.mkdir()
    marker = tmp_path / "ran"
    (lab / "rodeo-plan.yaml").write_text(f'registry:\n  token: "??cmd:touch {marker}"\n')
    seen = _stub_deploy_check(monkeypatch, tmp_path)
    result = CliRunner().invoke(
        deploy_mod.deploy_cmd, ["--config", str(lab / "rodeo-plan.yaml"), "--check"]
    )
    assert result.exit_code == 1
    assert "rodeo trust" in result.output
    assert "cfg" not in seen
    assert not marker.exists()


def test_deploy_refuses_untrusted_cloned_lab(monkeypatch, tmp_path):
    """Attack: a cloned lab; deploy refuses before anything resolves or runs."""
    from click.testing import CliRunner

    lab = tmp_path / "clone"
    lab.mkdir()
    marker = tmp_path / "ran"
    (lab / "rodeo-plan.yaml").write_text(
        f'credentials:\n  harvester_os_password: "??cmd:touch {marker} && echo pw"\n'
    )
    seen = _stub_deploy_check(monkeypatch, tmp_path)
    monkeypatch.setattr(deploy_mod, "execute_deploy", lambda *a, **k: seen.setdefault("ran", True))
    result = CliRunner().invoke(deploy_mod.deploy_cmd, ["--config", str(lab / "rodeo-plan.yaml")])
    assert result.exit_code == 1
    assert "rodeo trust" in result.output
    assert "ran" not in seen
    assert not marker.exists()
