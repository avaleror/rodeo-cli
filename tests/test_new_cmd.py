"""rodeo new — scaffold custom profiles; profile resolution; rodeo profiles."""
from __future__ import annotations

import yaml
from click.testing import CliRunner

from rodeo import labseed
from rodeo.commands.new_cmd import new_cmd
from rodeo.commands.profiles_cmd import profiles_cmd


def test_new_scaffolds_custom_profile(tmp_path):
    # HOME is tmp_path (conftest) -> ~/.rodeo/profiles/mylab
    result = CliRunner().invoke(new_cmd, ["mylab", "--from", "rancher"])
    assert result.exit_code == 0, result.output

    dest = tmp_path / ".rodeo" / "profiles" / "mylab"
    assert (dest / "rodeo-plan.yaml").exists()
    assert (dest / "definition.yaml").exists()

    plan = yaml.safe_load((dest / "rodeo-plan.yaml").read_text())
    assert plan["name"] == "mylab"
    assert plan["type"] == "rancher"

    # Definition retitled, comments preserved (header + base comments still present).
    text = (dest / "definition.yaml").read_text()
    assert "Custom rodeo 'mylab'" in text
    assert yaml.safe_load(text)["definition"]["name"] == "mylab"


def test_new_rejects_unknown_base():
    result = CliRunner().invoke(new_cmd, ["x", "--from", "nope"])
    assert result.exit_code != 0  # click.Choice rejects it


def test_new_refuses_overwrite_without_force(tmp_path):
    runner = CliRunner()
    runner.invoke(new_cmd, ["dup", "--from", "rancher"])
    result = runner.invoke(new_cmd, ["dup", "--from", "rancher"])
    assert result.exit_code == 1
    assert "already exists" in result.output

    forced = runner.invoke(new_cmd, ["dup", "--from", "rancher", "--force"])
    assert forced.exit_code == 0, forced.output


def test_profile_resolution(tmp_path):
    assert labseed.profile_kind("harvester") == "bundled"
    assert labseed.profile_kind("ghost") is None

    labseed.scaffold_profile("custom1", from_base="rancher")
    assert labseed.profile_kind("custom1") == "custom"
    assert labseed.resolve_profile_source("custom1") == labseed.custom_profile_dir("custom1")

    names = {p["name"]: p["kind"] for p in labseed.list_profiles()}
    assert names["harvester"] == "bundled"
    assert names["custom1"] == "custom"


def test_profiles_command_lists_custom(tmp_path):
    labseed.scaffold_profile("listed", from_base="rancher")
    result = CliRunner().invoke(profiles_cmd, [])
    assert result.exit_code == 0, result.output
    assert "listed" in result.output
    assert "harvester" in result.output
