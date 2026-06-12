"""PXE/iPXE integration guards for the bundled Ansible tree."""
from __future__ import annotations

from pathlib import Path

import yaml

import rodeo
from rodeo.profiles.suse_virt import SuseVirtProfile

_DATA = Path(rodeo.__file__).parent / "data"
_ANSIBLE = _DATA / "ansible"


def test_playbook_includes_pxe_server_role():
    playbook = yaml.safe_load((_ANSIBLE / "playbook.yml").read_text())
    roles = [r["role"] for r in playbook[0]["roles"]]
    assert roles == ["kvm_host", "vms", "pxe_server"]


def test_pxe_server_role_is_complete():
    role = _ANSIBLE / "roles" / "pxe_server"
    for rel in (
        "tasks/main.yml",
        "tasks/files.yml",
        "tasks/network.yml",
        "templates/ipxe-node.j2",
        "templates/config-node.yaml.j2",
        "templates/network-pxe.xml.j2",
    ):
        assert (role / rel).is_file(), rel


def test_profile_phases_include_pxe_server():
    profile = SuseVirtProfile()
    assert "pxe_server" in profile.phases
    assert profile.phases.index("pxe_server") == profile.phases.index("vms") + 1
    assert "pxe_server" in profile.ansible_phases


def test_vm_boot_order_disk_before_nic():
    xml = (_ANSIBLE / "roles" / "vms" / "templates" / "vm.xml.j2").read_text()
    assert "<boot order='1'/>" in xml
    assert "boot order='2'" in xml


def test_join_config_omits_vip():
    tpl = (_ANSIBLE / "roles" / "pxe_server" / "templates" / "config-node.yaml.j2").read_text()
    assert "first_harvester.name" in tpl
    assert "{% if item.name == first_harvester.name %}" in tpl
    assert "vip_mode: static" in tpl