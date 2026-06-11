"""Drift guard: the Ansible role defaults, the Python profile, and the plan
template describe the same lab. These values are coupled across layers
(MAC <-> DHCP lease <-> node IP <-> Harvester config ISO <-> VIP) and a
mismatch only surfaces 40+ minutes into a nested-KVM deploy. This test
makes drift a CI failure instead.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import rodeo
from rodeo.config import _BASE_DEFAULTS
from rodeo.profiles.suse_virt import SuseVirtProfile

_DATA = Path(rodeo.__file__).parent / "data"


@pytest.fixture(scope="module")
def role_defaults() -> dict:
    path = _DATA / "ansible" / "roles" / "vms" / "defaults" / "main.yml"
    return yaml.safe_load(path.read_text())


@pytest.fixture(scope="module")
def plan_template() -> dict:
    path = _DATA / "templates" / "rodeo-plan.yaml"
    return yaml.safe_load(path.read_text())


@pytest.fixture(scope="module")
def profile_cfg() -> dict:
    return SuseVirtProfile().default_cfg()


def test_vm_names_match_profile(role_defaults, profile_cfg):
    role_names = [n["name"] for n in role_defaults["vm_nodes"]]
    assert role_names == SuseVirtProfile.vm_names
    assert role_names == list(profile_cfg["vms"].keys())


def test_vm_ips_match_profile(role_defaults, profile_cfg):
    for node in role_defaults["vm_nodes"]:
        assert node["ip"] == profile_cfg["vms"][node["name"]]["ip"], node["name"]


def test_vip_is_not_a_node_ip(role_defaults):
    node_ips = {n["ip"] for n in role_defaults["vm_nodes"]}
    assert role_defaults["harvester_vip"] not in node_ips


def test_network_values_match_base_defaults(role_defaults):
    net = _BASE_DEFAULTS["network"]
    assert role_defaults["harvester_vip"] == net["vip"]
    assert role_defaults["libvirt_network_gateway"] == net["gateway"]
    assert role_defaults["lab_dns_domain"] == net["dns_domain"]
    assert role_defaults["image_dir"] == _BASE_DEFAULTS["storage"]["image_dir"]


def test_flavors_match_profile_resources(role_defaults, profile_cfg):
    assert role_defaults["libvirt_flavors"]["harvester"] == profile_cfg["resources"]["harvester"]
    assert role_defaults["libvirt_flavors"]["rancher"] == profile_cfg["resources"]["rancher"]


def test_harvester_version_matches_profile(role_defaults, profile_cfg):
    assert role_defaults["harvester_version"] == profile_cfg["versions"]["harvester"]


def test_macs_and_uuids_are_unique(role_defaults):
    macs, uuids = [], []
    for node in role_defaults["vm_nodes"]:
        uuids.append(node["uuid"])
        macs.extend(v for k, v in node.items() if k.endswith("_mac"))
    assert len(macs) == len(set(macs)), "duplicate MAC address in vm_nodes"
    assert len(uuids) == len(set(uuids)), "duplicate UUID in vm_nodes"


def test_plan_template_matches_defaults(plan_template, role_defaults, profile_cfg):
    net = plan_template["network"]
    assert net["vip"] == role_defaults["harvester_vip"]
    assert net["gateway"] == role_defaults["libvirt_network_gateway"]
    assert net["dns_domain"] == role_defaults["lab_dns_domain"]
    assert plan_template["storage"]["image_dir"] == role_defaults["image_dir"]
    assert plan_template["versions"]["harvester"] == role_defaults["harvester_version"]
    assert plan_template["resources"]["harvester"] == profile_cfg["resources"]["harvester"]
    assert plan_template["resources"]["rancher"] == profile_cfg["resources"]["rancher"]
    assert plan_template["versions"] == profile_cfg["versions"]


def test_rancher_ip_consistent(role_defaults, plan_template, profile_cfg):
    role_rancher = next(n for n in role_defaults["vm_nodes"] if n["name"] == "rancher")
    assert role_rancher["ip"] == plan_template["network"]["rancher_ip"]
    assert role_rancher["ip"] == profile_cfg["vms"]["rancher"]["ip"]
    assert role_rancher["ip"] == _BASE_DEFAULTS["network"]["rancher_ip"]
