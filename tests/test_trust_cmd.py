from __future__ import annotations

import yaml
from click.testing import CliRunner

from rodeo import config
from rodeo.cli import cli


def _plan(tmp_path, spec):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text(yaml.dump({"credentials": {"harvester_os_password": spec}}, width=10_000))
    return plan


def _tty(monkeypatch, answer=True):
    monkeypatch.setattr("rodeo.commands.trust_cmd._interactive", lambda: True)
    monkeypatch.setattr("rodeo.commands.trust_cmd.click.confirm", lambda *a, **k: answer)


def test_trust_refuses_without_tty(tmp_path):
    plan = _plan(tmp_path, "??cmd:echo hi")
    result = CliRunner().invoke(cli, ["trust", "--config", str(plan)], input="y\n")
    assert result.exit_code != 0
    assert "interactive terminal" in result.output
    assert config.load_trusted_labs() == {}


def test_trust_approves_on_tty_and_shows_what_the_lab_does(tmp_path, monkeypatch):
    plan = _plan(tmp_path, "??cmd:echo hi")
    (tmp_path / "custom" / "scripts").mkdir(parents=True)
    (tmp_path / "custom" / "scripts" / "10-setup.sh").write_text("echo hi\n")
    _tty(monkeypatch)
    result = CliRunner().invoke(cli, ["trust", "--config", str(plan)])
    assert result.exit_code == 0, result.output
    assert "??cmd:echo hi" in result.output
    assert "custom/scripts/10-setup.sh" in result.output
    assert config.is_lab_trusted(tmp_path)


def test_trust_yes_works_without_tty(tmp_path):
    """Instruqt setup scripts and CI approve a lab with --yes."""
    plan = _plan(tmp_path, "??cmd:echo hi")
    result = CliRunner().invoke(cli, ["trust", "--yes", "--config", str(plan)])
    assert result.exit_code == 0, result.output
    assert config.is_lab_trusted(tmp_path)
    cfg = config.load_config(plan, secrets="all")
    assert cfg["credentials"]["harvester_os_password"] == "hi"


def test_trust_ignores_params(tmp_path, monkeypatch):
    plan = _plan(tmp_path, "Secret123")
    _tty(monkeypatch)
    result = CliRunner().invoke(
        cli, ["trust", "--config", str(plan), "-P", "network.vip=10.9.9.9"]
    )
    assert result.exit_code == 0, result.output
    assert "Ignoring -P" in result.output


def test_trust_does_not_run_the_command(tmp_path, monkeypatch):
    marker = tmp_path / "pwned"
    plan = _plan(tmp_path, f"??cmd:touch {marker}")
    _tty(monkeypatch)
    CliRunner().invoke(cli, ["trust", "--config", str(plan)])
    assert not marker.exists()


def test_trust_list_shows_lab(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    _plan(lab, "Secret123")
    config.trust_lab(lab)
    result = CliRunner().invoke(cli, ["trust", "--list"])
    assert str(lab) in result.output


def test_trust_revoke(tmp_path, monkeypatch):
    plan = _plan(tmp_path, "Secret123")
    config.trust_lab(tmp_path)
    _tty(monkeypatch)
    result = CliRunner().invoke(cli, ["trust", "--revoke", "--config", str(plan)])
    assert result.exit_code == 0, result.output
    assert "Revoked" in result.output
    assert not config.is_lab_trusted(tmp_path)


def test_trust_revoke_needs_tty_or_yes(tmp_path):
    plan = _plan(tmp_path, "Secret123")
    config.trust_lab(tmp_path)
    result = CliRunner().invoke(cli, ["trust", "--revoke", "--config", str(plan)])
    assert result.exit_code != 0
    assert config.is_lab_trusted(tmp_path)


def test_trust_corrupt_file_is_error(tmp_path):
    path = tmp_path / ".rodeo" / "trusted-labs.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("- /some/lab\n")
    result = CliRunner().invoke(cli, ["trust", "--list"])
    assert result.exit_code != 0
    assert "corrupt" in result.output


def test_obsolete_per_string_file_warns(tmp_path):
    old = tmp_path / ".rodeo" / "trusted-sources.yaml"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("- ??cmd:echo hi\n")
    result = CliRunner().invoke(cli, ["trust", "--list"])
    assert "obsolete" in result.output


def test_mcp_does_not_offer_trust():
    from rodeo.mcp.tools import READ_TOOLS, WRITE_TOOLS

    tools = READ_TOOLS + WRITE_TOOLS
    assert tools
    assert not [t for t in tools if "trust" in t.name or "trust" in t.command]
