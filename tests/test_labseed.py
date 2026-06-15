"""Seeding a beginner-safe lab from a bundled example."""
from __future__ import annotations

import yaml

from rodeo.labseed import PROFILE_EXAMPLE, example_dir, seed_lab


def test_profile_maps_to_bundled_example():
    assert PROFILE_EXAMPLE["test"] == "harvester-lab-config"
    assert example_dir("test").is_dir()


def test_harvester_ha_profile(tmp_path):
    import yaml
    assert PROFILE_EXAMPLE["harvester-ha"] == "harvester-ha-config"
    lab = seed_lab("harvester-ha", tmp_path / "ha")
    defn = yaml.safe_load((lab / "definition.yaml").read_text())["definition"]
    names = [n["name"] for n in defn["nodes"]]
    assert names == ["harvester1", "harvester2", "harvester3"]
    assert "rancher" not in defn["start_order"]
    assert defn["harvester_ready_count"] == 3
    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    assert plan["resources"]["harvester"]["disk_gb"] == 250
    assert "rancher" not in plan.get("resources", {})


def test_seed_lab_normalizes_plan(tmp_path):
    lab = seed_lab("test", tmp_path / "labs" / "mylab")
    plan = lab / "rodeo-plan.yaml"
    assert plan.exists()
    assert (lab / "definition.yaml").exists()

    data = yaml.safe_load(plan.read_text())
    assert data["name"] == "mylab"
    assert data["deployment_target"] == "baremetal"
    # Single-disk safe: no inherited host device.
    assert data.get("storage", {}).get("device", "") == ""
    # File-form credentials (??key), never ??env:.
    for val in data.get("credentials", {}).values():
        assert isinstance(val, str) and val.startswith("??")
        assert not val.startswith("??env:")


def test_seed_lab_preserves_existing_files_without_force(tmp_path):
    lab = tmp_path / "labs" / "x"
    seed_lab("test", lab)
    (lab / "definition.yaml").write_text("definition:\n  name: edited\n")
    seed_lab("test", lab, force=False)  # must not clobber existing files
    assert "edited" in (lab / "definition.yaml").read_text()
