"""Seeding a beginner-safe lab from a bundled example."""
from __future__ import annotations

import yaml

from rodeo.labseed import (
    PROFILE_EXAMPLE,
    example_dir,
    seed_lab,
)


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


def test_harvester_with_aws_target_applies_host_context(tmp_path):
    """Option A: same harvester topology; AWS is deployment_target + host_context."""
    assert "harvester-aws" not in PROFILE_EXAMPLE

    lab = seed_lab("harvester", tmp_path / "harvester", deployment_target="aws")

    defn = yaml.safe_load((lab / "definition.yaml").read_text())["definition"]
    assert [n["name"] for n in defn["nodes"]] == [
        "harvester1",
        "harvester2",
        "harvester3",
        "rancher",
    ]

    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    assert plan["deployment_target"] == "aws"
    # apply_host_context() ran at seed time: flat per-node floor.
    assert plan["resources"]["harvester"]["disk_gb"] == 500
    assert plan["resources"]["rancher"]["disk_gb"] == 60
    assert plan["storage"]["backend"] == "nvme"
    assert "provider" not in plan  # fill in for acquire, or use CLI


def test_virt_workshop_aws_profile_has_custom_scripts(tmp_path):
    """virt-workshop-aws is harvester topology plus custom/scripts/ that
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


def test_virt_workshop_aws_2n_profile_is_2_node_and_has_custom_scripts(tmp_path):
    """virt-workshop-aws-2n is the budget tier of virt-workshop-aws: 2-node
    Harvester (like harvester-2n) instead of 3, sized for a smaller instance
    (m8id.4xlarge) via disk_floor_override_gb, same custom/scripts/ minus the
    3rd node's stage=dev label."""
    assert PROFILE_EXAMPLE["virt-workshop-aws-2n"] == "virt-workshop-aws-2n"
    assert example_dir("virt-workshop-aws-2n").is_dir()

    lab = seed_lab("virt-workshop-aws-2n", tmp_path / "virt-workshop-aws-2n", deployment_target="aws")

    defn = yaml.safe_load((lab / "definition.yaml").read_text())["definition"]
    assert [n["name"] for n in defn["nodes"]] == ["harvester1", "harvester2", "rancher"]

    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    assert plan["deployment_target"] == "aws"
    # disk_floor_override_gb (400) wins over the global AWS floor (500) —
    # apply_host_context() ran at seed time.
    assert plan["resources"]["harvester"]["disk_gb"] == 400
    assert plan["resources"]["harvester"]["disk_floor_override_gb"] == 400
    assert plan["resources"]["harvester"]["memory_mib"] == 16384
    assert plan["resources"]["rancher"]["disk_gb"] == 60

    scripts_dir = lab / "custom" / "scripts"
    scripts = sorted(f.name for f in scripts_dir.iterdir() if f.is_file())
    assert scripts == [
        "50-image-cache.sh",
        "60-nfs-backup-target.sh",
        "70-webserver-prod.sh",
    ]
    for name in scripts:
        assert (scripts_dir / name).stat().st_mode & 0o111, f"{name} must be executable"

    script_text = (scripts_dir / "70-webserver-prod.sh").read_text()
    assert "harvester3" not in script_text
    assert "kubectl label node harvester3" not in script_text
    assert "stage=dev" not in "\n".join(
        line for line in script_text.splitlines() if not line.lstrip().startswith("#")
    )


def test_suse_edge_with_aws_target_applies_host_context(tmp_path):
    """Option A: suse-edge + aws host context; no separate *-aws profile.

    aws host-context must force self-signed TLS, not the bare-metal default
    of letsEncrypt: rodeo's managed security group (rodeo/providers/aws.py
    MANAGED_SG_PORTS) only opens 22/8443/30002 to the operator's own IP, so
    the ACME HTTP-01 challenge (port 80) could never complete. See
    test_host_context.py for the full overlay behavior."""
    assert "suse-edge-aws" not in PROFILE_EXAMPLE

    lab = seed_lab("suse-edge", tmp_path / "suse-edge-on-aws", deployment_target="aws")

    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    assert plan["type"] == "suse-edge"
    assert plan["deployment_target"] == "aws"
    assert plan["storage"]["backend"] == "nvme"
    assert plan["resources"]["rancher"]["memory_mib"] == 12288
    assert plan["resources"]["eib"]["memory_mib"] == 16384
    assert plan["rancher_tls"]["source"] == "secret"

    edge_lab = seed_lab("suse-edge", tmp_path / "suse-edge", deployment_target="baremetal")
    edge_plan = yaml.safe_load((edge_lab / "rodeo-plan.yaml").read_text())
    assert edge_plan["rancher_tls"]["source"] == "letsEncrypt"
    assert edge_plan["resources"]["rancher"]["memory_mib"] == 8192


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


def test_no_bundled_profile_reaches_aws_with_letsencrypt(tmp_path):
    """Coherence invariant, not a per-profile test: rodeo's own managed
    security group (rodeo/providers/aws.py MANAGED_SG_PORTS) only ever opens
    22/8443/30002 to the operator's own IP — never 80 or 443. So no bundled
    profile may resolve rancher_tls.source to 'letsEncrypt' once seeded for
    deployment_target=aws, no matter what the profile's own bare-metal
    default is. This is what should have caught the 2026-09-16 suse-edge
    regression: that bug shipped because the test covering it was rewritten
    to match the new (broken) value instead of asserting this rule. Any
    *future* profile that ships (or grows) a rancher_tls default must clear
    this bar too, automatically, without anyone remembering to special-case it."""
    for profile in PROFILE_EXAMPLE:
        lab = seed_lab(profile, tmp_path / f"aws-{profile}", deployment_target="aws")
        plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
        tls = plan.get("rancher_tls")
        if tls is not None:
            assert tls.get("source") != "letsEncrypt", (
                f"profile {profile!r} seeds rancher_tls.source=letsEncrypt on aws, "
                "but the managed security group never opens port 80/443 — the "
                "ACME challenge can never complete. Use a self-signed source "
                "(e.g. 'secret') via a host_context.py aws overlay instead."
            )
