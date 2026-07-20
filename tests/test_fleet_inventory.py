"""Tests for fleet inventory loading and host selection."""
from __future__ import annotations

import textwrap

import pytest

from rodeo.config import ConfigError
from rodeo.fleet.inventory import load_inventory, parse_label_opts, select_hosts


def _write_workshop(tmp_path, body: str):
    path = tmp_path / "workshop.yaml"
    path.write_text(textwrap.dedent(body))
    return path


def test_load_inventory_ok(tmp_path):
    path = _write_workshop(
        tmp_path,
        """
        name: demo
        lab:
          dir: /root/lab
        defaults:
          ssh_user: root
        hosts:
          - id: a
            ssh: 10.0.0.1
            labels: {room: x}
          - id: b
            ssh: user@10.0.0.2
            public_ip: 203.0.113.2
        """,
    )
    inv = load_inventory(path)
    assert inv.name == "demo"
    assert inv.lab_dir == "/root/lab"
    assert inv.ssh_user == "root"
    assert len(inv.hosts) == 2
    assert inv.hosts[0].id == "a"
    assert inv.hosts[1].public_ip == "203.0.113.2"


def test_load_inventory_duplicate_id(tmp_path):
    path = _write_workshop(
        tmp_path,
        """
        lab:
          dir: /root/lab
        hosts:
          - id: a
            ssh: 10.0.0.1
          - id: a
            ssh: 10.0.0.2
        """,
    )
    with pytest.raises(ConfigError, match="duplicate"):
        load_inventory(path)


def test_load_inventory_requires_lab_dir(tmp_path):
    path = _write_workshop(
        tmp_path,
        """
        hosts:
          - id: a
            ssh: 10.0.0.1
        """,
    )
    with pytest.raises(ConfigError, match="lab.dir"):
        load_inventory(path)


def test_select_hosts_by_label_and_id(tmp_path):
    path = _write_workshop(
        tmp_path,
        """
        lab:
          dir: /root/lab
        hosts:
          - id: a
            ssh: 10.0.0.1
            labels: {room: a, track: virt}
          - id: b
            ssh: 10.0.0.2
            labels: {room: b, track: virt}
          - id: c
            ssh: 10.0.0.3
            labels: {room: a, track: edge}
        """,
    )
    inv = load_inventory(path)
    room_a = select_hosts(inv, labels={"room": "a"})
    assert [h.id for h in room_a] == ["a", "c"]
    both = select_hosts(inv, ids=["a", "b"], labels={"room": "a"})
    assert [h.id for h in both] == ["a"]


def test_select_hosts_unknown_id(tmp_path):
    path = _write_workshop(
        tmp_path,
        """
        lab:
          dir: /root/lab
        hosts:
          - id: a
            ssh: 10.0.0.1
        """,
    )
    inv = load_inventory(path)
    with pytest.raises(ConfigError, match="unknown host"):
        select_hosts(inv, ids=["missing"])


def test_parse_label_opts():
    assert parse_label_opts(("room=a", "track=virt")) == {"room": "a", "track": "virt"}
    with pytest.raises(ConfigError, match="key=value"):
        parse_label_opts(("nocolon",))
