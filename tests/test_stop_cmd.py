from __future__ import annotations

from click.testing import CliRunner

import rodeo.commands.stop_cmd as stop_mod
from rodeo.commands.stop_cmd import stop_cmd


def test_stop_with_yes_and_all_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(stop_mod, "is_root", lambda: True)
    result = CliRunner().invoke(stop_cmd, ["--yes", "--all"])
    assert result.exit_code == 0, result.output
    assert "Stop complete" in result.output or "falling back" in result.output


def test_stop_with_yes_uses_plan_name(tmp_path, monkeypatch):
    monkeypatch.setattr(stop_mod, "is_root", lambda: True)
    result = CliRunner().invoke(stop_cmd, ["--yes", "--config", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 0, result.output
    assert "Stop complete" in result.output or "falling back" in result.output
