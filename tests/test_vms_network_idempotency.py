"""vms/tasks/network_setup.yml must not destroy+redefine the default libvirt
network on every re-run — that flaps DHCP/DNS on virbr0 needlessly and, unlike
pxe_server/tasks/network.yml (which guards the same destroy+redefine), used to
run unconditionally. This pins the per-node DHCP host guard so it can't regress.
"""
from __future__ import annotations

from pathlib import Path

import yaml

import rodeo

_TASKS = Path(rodeo.__file__).parent / "data" / "ansible" / "roles" / "vms" / "tasks" / "network_setup.yml"


def _outer_block() -> list:
    doc = yaml.safe_load(_TASKS.read_text())
    assert doc[0]["when"] == "network_mode == 'nat'"
    return doc[0]["block"]


def test_network_xml_is_dumped_before_any_redefinition():
    block = _outer_block()
    assert block[0]["name"] == "Dump current network XML"
    assert block[0]["register"] == "_current_net_xml"
    assert block[0]["changed_when"] is False


def test_destroy_and_redefine_are_guarded_per_node_not_unconditional():
    block = _outer_block()
    compute = block[1]
    assert compute["name"] == "Compute whether planned DHCP hosts match the live network"
    assert "_net_needs_redefine" in compute["ansible.builtin.set_fact"]
    fact = compute["ansible.builtin.set_fact"]["_net_needs_redefine"]
    assert "mgmt_mac" in fact
    assert "node.ip" in fact

    guarded = block[2]
    assert guarded["name"] == "Redefine network with static DHCP entries (skipped if plan matches)"
    assert "_net_needs_redefine" in guarded["when"]

    inner_names = [t["name"] for t in guarded["block"]]
    assert "Destroy default network to allow redefinition with static DHCP entries" in inner_names
    assert "Define default network with static DHCP entries" in inner_names


def test_running_domain_check_fails_closed_on_virsh_errors():
    """virsh list failure must block redefine (no failed_when: false)."""
    block = _outer_block()
    inner = block[2]["block"]
    check = next(t for t in inner if t["name"].startswith("Check for running domains"))
    assert "failed_when" not in check
    fail = next(t for t in inner if t["name"].startswith("Fail if virsh"))
    assert "running_domains.rc != 0" in fail["when"]


def test_network_start_remains_unconditional():
    """The final activation must run whether or not the guard skipped
    redefinition — a skipped redefine still needs the network active."""
    block = _outer_block()
    last = block[-1]
    assert last["name"] == "Start default network (active now; autostart disabled until phase 3)"
    assert "when" not in last
