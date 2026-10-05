"""suse-telco registration: plan renders, catalog does not fall back to Harvester."""
from __future__ import annotations

import re

import yaml
from click.testing import CliRunner

from rodeo.commands.plan_cmd import plan_cmd
from rodeo.inventory import build_inventory
from rodeo.labseed import PROFILE_EXAMPLE, seed_lab
from rodeo.profiles import get_profile
from rodeo.providers.instance_catalog import catalog_for_profile

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _flat(output: str) -> str:
    return " ".join(_ANSI.sub("", output).split())


def test_profile_phase_order_and_edge_unchanged():
    telco = get_profile("suse-telco")
    assert telco.phases == [
        "kvm_host",
        "images",
        "vms",
        "boot",
        "enroll",
        "finalise",
        "custom_scripts",
    ]
    assert telco.ansible_phases == frozenset()
    assert "images" in telco.no_cache_phases
    assert "enroll" in telco.no_cache_phases
    edge = get_profile("suse-edge")
    assert edge.phases == [
        "kvm_host",
        "vms",
        "boot",
        "rancher",
        "elemental",
        "apply",
        "finalise",
        "custom_scripts",
    ]


def test_definition_topology():
    inv = build_inventory({"type": "suse-telco", "name": "suse-telco-test"})
    assert [n["name"] for n in inv["vm_nodes"]] == ["mgmt", "site-co", "site-ran"]
    assert [n["flavor"] for n in inv["vm_nodes"]] == ["mgmt", "site", "site"]
    net = inv["libvirt_network"]
    assert net["name"] == "telco"
    assert net["bridge"] == "virbr125"
    assert net["cidr"] == "192.168.125.0/24"
    assert net["gateway"] == "192.168.125.1"
    assert {h["name"]: h["ip"] for h in net["dhcp_hosts"]} == {
        "mgmt": "192.168.125.10",
        "site-co": "192.168.125.21",
        "site-ran": "192.168.125.22",
    }


def test_plan_renders_telco_topology(tmp_path):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("type: suse-telco\nname: suse-telco-test\n")
    result = CliRunner().invoke(plan_cmd, ["--config", str(plan)])
    out = _flat(result.output)
    assert result.exit_code == 0, result.output
    assert "mgmt" in out
    assert "site-co" in out
    assert "site-ran" in out
    assert "16384 MiB / 8 vcpu" in out
    assert "8192 MiB / 4 vcpu" in out
    for phase in ("kvm_host", "images", "vms", "boot", "enroll", "finalise", "custom_scripts"):
        assert phase in out
    assert "elemental pending" not in out
    assert "pxe_server pending" not in out


def test_aws_catalog_does_not_fall_back_to_harvester():
    telco = catalog_for_profile("suse-telco")
    harvester = catalog_for_profile("harvester")
    assert telco["recommended"].instance_type == "m8id.4xlarge"
    assert telco["performance"].instance_type == "m7i.metal-24xl"
    assert harvester["recommended"].instance_type == "m8id.8xlarge"
    assert telco is not harvester


def test_example_is_registered_and_seeds(tmp_path):
    assert PROFILE_EXAMPLE["suse-telco"] == "suse-telco"
    lab = seed_lab("suse-telco", tmp_path / "suse-telco")
    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    assert plan["type"] == "suse-telco"
    assert plan["deployment_target"] == "baremetal"
    assert plan["network"]["dns_domain"] == "northline.telco"
    assert plan["network"]["gateway"] == "192.168.125.1"
    assert plan["rancher_tls"]["source"] == "secret"
    assert plan["resources"]["mgmt"]["memory_mib"] == 16384
    assert plan["resources"]["site"]["vcpu"] == 4
