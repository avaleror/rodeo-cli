"""pxe_server/tasks/network.yml must guard destroy+redefine the same way
vms/tasks/network_setup.yml does — PXE dnsmasq options + per-node DHCP hosts.
Pins the guard so it can't regress to an unconditional virbr0 flap.
"""
from __future__ import annotations

from pathlib import Path

import yaml

import rodeo

_TASKS = (
    Path(rodeo.__file__).parent
    / "data"
    / "ansible"
    / "roles"
    / "pxe_server"
    / "tasks"
    / "network.yml"
)


def _tasks() -> list:
    return yaml.safe_load(_TASKS.read_text())


def test_network_xml_is_dumped_before_any_redefinition():
    tasks = _tasks()
    assert tasks[0]["name"] == "Dump current network XML"
    assert tasks[0]["register"] == "_current_net_xml"
    assert tasks[0]["changed_when"] is False


def test_destroy_and_redefine_are_guarded_on_pxe_opts_and_per_node_dhcp():
    tasks = _tasks()
    compute = tasks[1]
    assert compute["name"] == "Compute whether PXE network XML matches plan"
    assert "_pxe_net_needs_redefine" in compute["ansible.builtin.set_fact"]
    fact = compute["ansible.builtin.set_fact"]["_pxe_net_needs_redefine"]
    assert "dnsmasq:options" in fact
    assert "mgmt_mac" in fact
    assert "node.ip" in fact

    guarded = tasks[2]
    assert guarded["name"] == "Redefine network with PXE options (skipped if plan matches)"
    assert "_pxe_net_needs_redefine" in guarded["when"]

    inner_names = [t["name"] for t in guarded["block"]]
    assert "Destroy default network to allow redefinition" in inner_names
    assert "Define default network with PXE dnsmasq options" in inner_names


def test_running_domain_check_fails_closed_on_virsh_errors():
    """virsh list failure must block redefine (no failed_when: false)."""
    tasks = _tasks()
    inner = tasks[2]["block"]
    check = next(t for t in inner if t["name"].startswith("Check for running domains"))
    assert "failed_when" not in check
    fail = next(t for t in inner if t["name"].startswith("Fail if virsh"))
    assert "_pxe_running_domains.rc != 0" in fail["when"]


def test_network_start_stays_inside_redefine_block():
    """Unlike vms/network_setup.yml, PXE start runs only when redefine ran —
    the network is already active when the guard skips."""
    tasks = _tasks()
    inner = tasks[2]["block"]
    start = next(t for t in inner if t["name"].startswith("Start default network"))
    assert start["community.libvirt.virt_net"]["autostart"] is False
    assert len(tasks) == 3
