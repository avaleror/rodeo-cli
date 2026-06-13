from __future__ import annotations

from click.testing import CliRunner

from rodeo.commands.start_cmd import start_cmd


def test_start_with_yes_and_all_runs(tmp_path):
    result = CliRunner().invoke(start_cmd, ["--yes", "--all"])
    assert result.exit_code == 0, result.output
    assert "Start complete" in result.output or "falling back" in result.output


def test_start_with_yes_uses_plan_name(tmp_path):
    result = CliRunner().invoke(start_cmd, ["--yes", "--config", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 0, result.output
    assert "Start complete" in result.output or "falling back" in result.output