from __future__ import annotations

import yaml
from click.testing import CliRunner

from rodeo import config
from rodeo.cli import cli


def _plan(tmp_path, spec):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text(yaml.dump({"credentials": {"harvester_os_password": spec}}))
    return plan


def test_trust_refuses_without_tty(tmp_path):
    plan = _plan(tmp_path, "??cmd:echo hi")
    result = CliRunner().invoke(cli, ["trust", "--config", str(plan)], input="y\n")
    assert result.exit_code != 0
    assert "interactive terminal" in result.output
    assert config.load_trusted_sources() == set()


def test_trust_approves_on_tty(tmp_path, monkeypatch):
    plan = _plan(tmp_path, "??cmd:echo hi")
    monkeypatch.setattr("rodeo.commands.trust_cmd._interactive", lambda: True)
    monkeypatch.setattr("rodeo.commands.trust_cmd.click.confirm", lambda *a, **k: True)
    result = CliRunner().invoke(cli, ["trust", "--config", str(plan)])
    assert result.exit_code == 0, result.output
    assert config.load_trusted_sources() == {"??cmd:echo hi"}


def test_trust_does_not_run_the_command(tmp_path):
    marker = tmp_path / "pwned"
    plan = _plan(tmp_path, f"??cmd:touch {marker}")
    CliRunner().invoke(cli, ["trust", "--config", str(plan)])
    assert not marker.exists()


def test_trust_list(tmp_path):
    config.save_trusted_sources({"??file:/run/secrets/pw"})
    result = CliRunner().invoke(cli, ["trust", "--list"])
    assert "??file:/run/secrets/pw" in result.output
