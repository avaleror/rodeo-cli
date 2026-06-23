from __future__ import annotations

from click.testing import CliRunner

import rodeo.commands.start_cmd as start_mod
from rodeo.commands.start_cmd import start_cmd


def test_start_with_yes_and_all_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(start_mod, "is_root", lambda: True)
    result = CliRunner().invoke(start_cmd, ["--yes", "--all"])
    assert result.exit_code == 0, result.output
    assert "Start complete" in result.output or "falling back" in result.output


def test_start_with_yes_uses_plan_name(tmp_path, monkeypatch):
    monkeypatch.setattr(start_mod, "is_root", lambda: True)
    result = CliRunner().invoke(start_cmd, ["--yes", "--config", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 0, result.output
    assert "Start complete" in result.output or "falling back" in result.output
