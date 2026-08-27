"""lab-in-a-box export — spec translation contract (rodeo/labinabox.py).

Pins the lab.json shape consumed by lab-in-a-box main (release 1.0.0):
node keys become per-VM env vars, common holds lab-wide defaults, kclusters
drives setup_k3s/setup_rke2 and install_<addon> dispatch.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from rodeo.cli import cli
from rodeo.config import ConfigError, load_config
from rodeo.labinabox import _reverse_zone, build_lab_json


def _rancher_cfg(tmp_path, extra_plan: str = ""):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("type: rancher\nname: demo\n" + extra_plan)
    return load_config(plan)


def test_rancher_profile_maps_to_lab_json(tmp_path):
    cfg = _rancher_cfg(tmp_path)
    lab, warnings = build_lab_json(cfg)

    # Node keyed by FQDN, with the definition's IP/MAC and pinned NETWORK.
    node = lab["nodes"]["rancher.rodeo.lab"]
    assert node["myip"] == "192.168.122.9"
    assert node["mymac"] == "02:00:00:0D:62:E9"
    assert node["NETWORK"] == "bridge=virbr0,mac.address=02:00:00:0D:62:E9"

    # Sizing from the profile's resources block, stringified for the shell.
    assert node["VM_MEM"] == "8192"
    assert node["VM_CPU"] == "4"
    assert node["VM_DSK"] == "60"

    # The rancher node forms the management cluster (K3s on this profile).
    assert node["kcluster"] == "mgmt"
    assert "INSTALL_RKE2_TYPE" not in node  # k3s role is decided by order
    kcluster = lab["kclusters"]["mgmt"]
    assert kcluster["clu_type"] == "k3s"
    assert kcluster["clu_rel"] == "stable"
    assert kcluster["addons"] == ["rancher"]

    # Common network defaults derived from the definition's libvirt network.
    common = lab["common"]
    assert common["lab_name"] == "demo"
    assert common["mymask"] == "24"
    assert common["mygw"] == "192.168.122.1"
    assert common["mydns"] == "192.168.122.1"
    assert common["mynet_reverse"] == "122.168.192"
    assert common["mydomain"] == "rodeo.lab"
    assert common["config_method"] == "cloud-init"

    # Rancher addon section from the plan's versions.
    assert lab["rancher"]["rancher_version"] == cfg["versions"]["rancher"]
    assert lab["rancher"]["cert_manager_ver"] == f"--version {cfg['versions']['cert_manager']}"

    # No base image configured: exported with a warning, not an error.
    assert "ISO_IMAGE" not in common
    assert any("iso_image" in w for w in warnings)

    # The result must be plain JSON (setup_lab.sh validates with jq).
    json.loads(json.dumps(lab))


def test_pxe_nodes_are_rejected(tmp_path):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("type: suse-virt\nname: hv\n")
    cfg = load_config(plan)

    with pytest.raises(ConfigError, match="PXE"):
        build_lab_json(cfg)
    with pytest.raises(ConfigError, match="harvester1"):
        build_lab_json(cfg)


def test_skip_unsupported_drops_pxe_nodes(tmp_path):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("type: suse-virt\nname: hv\n")
    cfg = load_config(plan)

    lab, warnings = build_lab_json(cfg, skip_unsupported=True)
    assert list(lab["nodes"]) == ["rancher.rodeo.lab"]
    assert any("skipped PXE" in w and "harvester1" in w for w in warnings)


def test_overlay_overrides(tmp_path):
    cfg = _rancher_cfg(
        tmp_path,
        "lab_in_a_box:\n"
        "  iso_image: openSUSE-Leap-15.6.qcow2\n"
        "  cluster_name: lab1\n"
        "  cluster_type: rke2\n"
        "  clu_rel: latest\n"
        "  addons: [rancher, longhorn]\n"
        "  sections:\n"
        "    longhorn: {lh_rel: '1.7.0'}\n"
        "    rancher: {rancher_rel: stable}\n",
    )
    lab, _warnings = build_lab_json(cfg)

    assert lab["common"]["ISO_IMAGE"] == "openSUSE-Leap-15.6.qcow2"
    node = lab["nodes"]["rancher.rodeo.lab"]
    assert node["kcluster"] == "lab1"
    assert node["INSTALL_RKE2_TYPE"] == "server"
    kcluster = lab["kclusters"]["lab1"]
    assert kcluster["clu_type"] == "rke2"
    assert kcluster["clu_rel"] == "latest"
    assert kcluster["addons"] == ["rancher", "longhorn"]
    # sections: new blocks are added, known ones are merged (not replaced).
    assert lab["longhorn"] == {"lh_rel": "1.7.0"}
    assert lab["rancher"]["rancher_rel"] == "stable"
    assert lab["rancher"]["rancher_version"] == cfg["versions"]["rancher"]


def test_port_forward_warning(tmp_path):
    cfg = _rancher_cfg(tmp_path)
    _lab, warnings = build_lab_json(cfg)
    assert any("port-forwards" in w for w in warnings)


def test_reverse_zone():
    assert _reverse_zone("192.168.122.0/24") == "122.168.192"
    assert _reverse_zone("10.20.30.0/24") == "30.20.10"
    assert _reverse_zone("not-a-cidr") is None


def test_export_cmd_stdout_is_json(tmp_path, monkeypatch):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("type: rancher\nname: demo\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["export", "--format", "lab-in-a-box"])
    assert result.exit_code == 0, result.output
    lab = json.loads(result.stdout)
    assert "rancher.rodeo.lab" in lab["nodes"]


def test_export_cmd_writes_file(tmp_path, monkeypatch):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("type: rancher\nname: demo\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["export", "-o", "lab.json"])
    assert result.exit_code == 0, result.output
    lab = json.loads((tmp_path / "lab.json").read_text())
    assert lab["common"]["lab_name"] == "demo"


def test_export_cmd_fails_cleanly_on_pxe_profile(tmp_path, monkeypatch):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("type: suse-virt\nname: hv\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["export"])
    assert result.exit_code == 1
    assert "PXE" in result.output
