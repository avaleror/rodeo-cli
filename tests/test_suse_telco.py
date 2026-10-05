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
    assert net["name"] == "default"
    assert net["bridge"] == "virbr0"
    assert net["cidr"] == "192.168.122.0/24"
    assert net["gateway"] == "192.168.122.1"
    assert {h["name"]: h["ip"] for h in net["dhcp_hosts"]} == {
        "mgmt": "192.168.122.10",
        "site-co": "192.168.122.21",
        "site-ran": "192.168.122.32",
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
    assert plan["network"]["gateway"] == "192.168.122.1"
    assert plan["rancher_tls"]["source"] == "rancher"
    assert plan["resources"]["mgmt"]["memory_mib"] == 16384
    assert plan["resources"]["site"]["vcpu"] == 4


def test_versions_match_telco_37_release_notes():
    v = get_profile("suse-telco").versions
    assert v["rancher"] == "2.15.1"
    assert v["rke2"] == "v1.36.3+rke2r1"
    assert v["cert_manager"] == "v1.20.1"  # cert-manager release tags carry the v
    assert v["turtles_providers"] == "307.0.8+up0.27.0"
    assert v["metal3"] == "307.0.31+up0.16.0"
    assert v["kubevirt"] == "307.0.3+up0.8.0"
    assert v["cdi"] == "307.0.3+up0.8.0"
    assert v["eib"] == "1.3.4"
    assert v["kiwi_builder"] == "10.2.29.1"


def test_example_plan_matches_profile_versions(tmp_path):
    lab = seed_lab("suse-telco", tmp_path / "suse-telco")
    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    profile = get_profile("suse-telco").versions
    for key, value in plan["versions"].items():
        assert profile[key] == value, key


def test_scc_code_is_operator_secret(tmp_path):
    from rodeo.secretgen import ensure_plan_secrets

    lab = seed_lab("suse-telco", tmp_path / "suse-telco")
    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    secrets = tmp_path / "secrets.yaml"
    generated, missing = ensure_plan_secrets(plan, path=secrets)
    assert "scc_registration_code" in missing
    assert "scc_registration_code" not in generated


def test_site_ran_vip_is_not_a_node_address():
    inv = build_inventory({"type": "suse-telco", "name": "suse-telco-test"})
    node_ips = {n["ip"] for n in inv["vm_nodes"]}
    sites = get_profile("suse-telco").extra_cfg()["telco"]["sites"]
    assert sites["site-co"]["control_plane_endpoint"] == "192.168.122.21"
    assert sites["site-ran"]["control_plane_endpoint"] == "192.168.122.22"
    assert "192.168.122.22" not in node_ips
    assert sites["site-ran"]["namespace"] == "northline"


def test_network_template_skips_empty_vip():
    from pathlib import Path

    import jinja2

    import rodeo

    tpl = Path(rodeo.__file__).parent / "data/ansible/roles/vms/templates/network.xml.j2"
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(tpl.parent)))
    base = {"lab_dns_domain": "northline.telco", "libvirt_network_gateway": "192.168.122.1", "vm_nodes": []}
    assert "<host ip=''>" not in env.get_template(tpl.name).render(harvester_vip="", **base)
    assert "virtualization.northline.telco" in env.get_template(tpl.name).render(
        harvester_vip="192.168.122.10", **base
    )
