"""vms/tasks/network_setup.yml must not destroy+redefine the default libvirt
network on every re-run — that flaps DHCP/DNS on virbr0 needlessly and, unlike
pxe_server/tasks/network.yml (which guards the same destroy+redefine), used to
run unconditionally. This pins the guard so it can't regress.
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


def test_destroy_and_redefine_are_guarded_not_unconditional():
    block = _outer_block()
    guarded = block[1]
    assert guarded["name"] == "Redefine network with static DHCP entries (skipped if already present)"
    assert "_current_net_xml" in guarded["when"]

    inner_names = [t["name"] for t in guarded["block"]]
    assert "Destroy default network to allow redefinition with static DHCP entries" in inner_names
    assert "Define default network with static DHCP entries" in inner_names


def test_network_start_remains_unconditional():
    """The final activation must run whether or not the guard skipped
    redefinition — a skipped redefine still needs the network active."""
    block = _outer_block()
    last = block[-1]
    assert last["name"] == "Start default network (active now; autostart disabled until phase 3)"
    assert "when" not in last
