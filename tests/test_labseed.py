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
    assert plan["resources"]["harvester"]["disk_gb"] == 320
    assert "rancher" not in plan.get("resources", {})


def test_harvester_aws_profile_same_topology_as_harvester(tmp_path):
    """harvester-aws is a distinct, AWS-pre-tuned profile — not a replacement
    for harvester. Same 3-node + Rancher topology, but its own provider block
    and disk sizing survive seeding with deployment_target=aws."""
    assert PROFILE_EXAMPLE["harvester-aws"] == "harvester-aws"
    assert example_dir("harvester-aws").is_dir()

    harvester_lab = seed_lab("harvester", tmp_path / "harvester", deployment_target="aws")
    aws_lab = seed_lab("harvester-aws", tmp_path / "harvester-aws", deployment_target="aws")

    harvester_defn = yaml.safe_load((harvester_lab / "definition.yaml").read_text())["definition"]
    aws_defn = yaml.safe_load((aws_lab / "definition.yaml").read_text())["definition"]
    assert [n["name"] for n in aws_defn["nodes"]] == [n["name"] for n in harvester_defn["nodes"]]

    plan = yaml.safe_load((aws_lab / "rodeo-plan.yaml").read_text())
    assert plan["deployment_target"] == "aws"
    assert plan["provider"]["type"] == "aws"
    assert plan["provider"]["instance_tier"] == "recommended"
    # apply_host_context() ran at seed time (deployment_target=aws): the flat
    # per-node floor, not the generic profile's 320.
    assert plan["resources"]["harvester"]["disk_gb"] == 500
    assert plan["resources"]["rancher"]["disk_gb"] == 60
    # RAM raised above the generic profile's 20/8 GiB 2026-09-11 for headroom:
    # 3x24 + 16 = 88 GiB guest RAM, live-verified to leave ~35 GiB of
    # m8id.8xlarge's ~123 GiB usable RAM for the host.
    assert plan["resources"]["harvester"]["memory_mib"] == 24576
    assert plan["resources"]["rancher"]["memory_mib"] == 16384

    # The generic "harvester" profile is untouched by adding "harvester-aws".
    harvester_plan = yaml.safe_load((harvester_lab / "rodeo-plan.yaml").read_text())
    assert "provider" not in harvester_plan


def test_virt_workshop_aws_profile_has_custom_scripts(tmp_path):
    """virt-workshop-aws is harvester-aws's infra plus custom/scripts/ that
    seed the pre-lab state suse-virt-workshop's exercises need (image cache,
    NFS backup target, pre-created webserver-prod VM)."""
    assert PROFILE_EXAMPLE["virt-workshop-aws"] == "virt-workshop-aws"
    assert example_dir("virt-workshop-aws").is_dir()

    lab = seed_lab("virt-workshop-aws", tmp_path / "virt-workshop-aws", deployment_target="aws")

    defn = yaml.safe_load((lab / "definition.yaml").read_text())["definition"]
    assert [n["name"] for n in defn["nodes"]] == ["harvester1", "harvester2", "harvester3", "rancher"]

    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    assert plan["deployment_target"] == "aws"
    assert plan["resources"]["harvester"]["disk_gb"] == 500
    assert plan["resources"]["harvester"]["memory_mib"] == 24576
    assert plan["resources"]["rancher"]["memory_mib"] == 16384

    scripts_dir = lab / "custom" / "scripts"
    scripts = sorted(f.name for f in scripts_dir.iterdir() if f.is_file())
    assert scripts == [
        "50-image-cache.sh",
        "60-nfs-backup-target.sh",
        "70-webserver-prod.sh",
    ]
    for name in scripts:
        assert (scripts_dir / name).stat().st_mode & 0o111, f"{name} must be executable"


def test_seed_lab_normalizes_plan(tmp_path):
    lab = seed_lab("test", tmp_path / "labs" / "mylab")
    plan = lab / "rodeo-plan.yaml"
    assert plan.exists()
    assert (lab / "definition.yaml").exists()

    data = yaml.safe_load(plan.read_text())
    assert data["name"] == "mylab"
    assert data["deployment_target"] == "baremetal"  # default
    # Single-disk safe: no inherited host device.
    assert data.get("storage", {}).get("device", "") == ""
    # File-form credentials (??key), never ??env:.
    for val in data.get("credentials", {}).values():
        assert isinstance(val, str) and val.startswith("??")
        assert not val.startswith("??env:")


def test_seed_lab_sets_instruqt_target(tmp_path):
    lab = seed_lab("test", tmp_path / "labs" / "iq", deployment_target="instruqt")
    data = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    assert data["deployment_target"] == "instruqt"


def test_seed_lab_preserves_existing_files_without_force(tmp_path):
    lab = tmp_path / "labs" / "x"
    seed_lab("test", lab)
    (lab / "definition.yaml").write_text("definition:\n  name: edited\n")
    seed_lab("test", lab, force=False)  # must not clobber existing files
    assert "edited" in (lab / "definition.yaml").read_text()
