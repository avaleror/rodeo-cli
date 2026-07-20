"""Tests for the laptop -> KVM-host OpenSSH argv builder."""
from __future__ import annotations

from rodeo.fleet.inventory import FleetHost, FleetInventory
from rodeo.fleet.ssh_exec import ssh_argv


def _inv(**defaults) -> FleetInventory:
    return FleetInventory(name="demo", lab_dir="/root/lab", defaults=defaults, hosts=[])


def test_ssh_argv_skips_host_key_verification():
    """Workshop hosts are freshly provisioned and unknown to the laptop's
    known_hosts; BatchMode=yes alone would fail the first connection to every
    host with "Host key verification failed" without this."""
    argv = ssh_argv(_inv(), FleetHost(id="h1", ssh="10.0.0.1"), "rodeo doctor")
    assert "StrictHostKeyChecking=no" in argv
    assert "UserKnownHostsFile=/dev/null" in argv
    assert "BatchMode=yes" in argv


def test_ssh_argv_uses_user_at_host_when_ssh_has_no_at():
    argv = ssh_argv(_inv(ssh_user="admin"), FleetHost(id="h1", ssh="10.0.0.1"), "cmd")
    assert "admin@10.0.0.1" in argv


def test_ssh_argv_respects_explicit_user_in_ssh_field():
    argv = ssh_argv(_inv(ssh_user="admin"), FleetHost(id="h1", ssh="root@10.0.0.1"), "cmd")
    assert "root@10.0.0.1" in argv
    assert "admin@10.0.0.1" not in argv


def test_ssh_argv_includes_identity_file_and_extra_options():
    argv = ssh_argv(
        _inv(identity_file="~/.ssh/id_ed25519", ssh_options=["ProxyJump=bastion.example"]),
        FleetHost(id="h1", ssh="10.0.0.1"),
        "cmd",
    )
    assert "-i" in argv
    assert "~/.ssh/id_ed25519" in argv
    assert "ProxyJump=bastion.example" in argv
