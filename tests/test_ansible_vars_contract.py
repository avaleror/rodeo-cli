"""Contract guard: every key the runner passes to ansible-playbook via
`-e @vars-file` must be consumed somewhere in the bundled Ansible tree
outside the role defaults. A key only present in defaults/main.yml means
the override is dead and the plan value silently does nothing — exactly
the bug where flat `harvester_memory_mb` keys were written while the
roles read nested `libvirt_flavors`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import rodeo
from rodeo.engine.runner import DeployRunner

_ANSIBLE = Path(rodeo.__file__).parent / "data" / "ansible"


@pytest.fixture(scope="module")
def ansible_text() -> str:
    """All Ansible sources except role defaults (defaults define, not consume)."""
    chunks = []
    for path in _ANSIBLE.rglob("*"):
        if path.is_file() and "defaults" not in path.parts:
            chunks.append(path.read_text(errors="replace"))
    return "\n".join(chunks)


def _vars_keys(tmp_path) -> dict:
    cfg = {
        "name": "contract",
        "credentials": {
            "harvester_os_password": "Secret123",
            "harvester_token": "tok-contract",
        },
        "network": {"vip": "192.168.122.10"},
    }
    vars_file = DeployRunner(cfg, tmp_path)._write_vars_file()
    return yaml.safe_load(vars_file.read_text())


def test_every_vars_key_is_consumed_by_ansible(tmp_path, ansible_text):
    data = _vars_keys(tmp_path)
    dead = [key for key in data if key not in ansible_text]
    assert not dead, (
        f"vars-file keys not referenced anywhere in rodeo/data/ansible "
        f"(outside defaults): {dead} — either wire them into the roles "
        f"or stop passing them."
    )


def test_flavors_shape_matches_role_defaults(tmp_path):
    """The nested override must have the same shape as the role default,
    otherwise Ansible merges nothing and falls back silently."""
    data = _vars_keys(tmp_path)
    role_defaults = yaml.safe_load(
        (_ANSIBLE / "roles" / "vms" / "defaults" / "main.yml").read_text()
    )
    assert set(data["libvirt_flavors"].keys()) == set(role_defaults["libvirt_flavors"].keys())
    for flavor, spec in role_defaults["libvirt_flavors"].items():
        assert set(data["libvirt_flavors"][flavor].keys()) == set(spec.keys()), flavor
