"""Lab directory auto-detection (walk-up)."""
from __future__ import annotations

from rodeo.config import find_lab_dir


def test_finds_lab_from_subdir(tmp_path):
    lab = tmp_path / "mylab"
    (lab / "certs").mkdir(parents=True)
    (lab / "rodeo-plan.yaml").write_text("name: x\n")
    assert find_lab_dir(lab / "certs") == lab.resolve()


def test_finds_lab_by_definition_marker(tmp_path):
    lab = tmp_path / "lab2"
    lab.mkdir()
    (lab / "definition.yaml").write_text("definition: {}\n")
    assert find_lab_dir(lab) == lab.resolve()


def test_returns_none_when_no_marker(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert find_lab_dir(empty) is None


def _remember_last_lab(tmp_path):
    from rodeo.paths import rodeo_last_lab_file

    old = tmp_path / "old-lab"
    old.mkdir()
    (old / "rodeo-plan.yaml").write_text("type: suse-edge\nname: old-lab\n")
    last = rodeo_last_lab_file()
    last.parent.mkdir(parents=True, exist_ok=True)
    last.write_text(str(old))
    return old


def test_falls_back_to_last_lab(tmp_path):
    old = _remember_last_lab(tmp_path)
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    assert find_lab_dir(empty) == old


def test_last_lab_fallback_can_be_skipped(tmp_path):
    _remember_last_lab(tmp_path)
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    assert find_lab_dir(empty, use_last_lab=False) is None


def test_up_with_profile_ignores_the_last_lab(tmp_path, monkeypatch):
    """`rodeo up --profile rancher` from any directory seeds the rancher lab,
    even when ~/.rodeo/last_lab points at a different lab (found live)."""
    import yaml
    from click.testing import CliRunner

    from rodeo.commands.up_cmd import up_cmd

    old = _remember_last_lab(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    result = CliRunner().invoke(
        up_cmd, ["--profile", "rancher", "--target", "aws", "--no-deploy", "--yes", "--no-tmux"]
    )
    assert result.exit_code == 0, result.output
    assert "Using existing lab" not in result.output
    plan = yaml.safe_load((tmp_path / "rodeo-labs" / "rancher" / "rodeo-plan.yaml").read_text())
    assert plan["type"] == "rancher"
    assert yaml.safe_load((old / "rodeo-plan.yaml").read_text())["type"] == "suse-edge"
