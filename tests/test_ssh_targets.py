"""Tests for rodeo ssh target resolution (vm | host | host/vm)."""
from __future__ import annotations

import textwrap

import pytest

from rodeo.config import ConfigError
from rodeo.ssh_targets import (
    build_ssh_target,
    default_identity,
    parse_ssh_target_arg,
    ssh_argv_for,
)


@pytest.fixture
def managed_ssh(tmp_path, monkeypatch):
    ssh_dir = tmp_path / "ssh"
    monkeypatch.setattr("rodeo.paths.rodeo_ssh_dir", lambda: ssh_dir)
    monkeypatch.setattr("rodeo.ssh_key.rodeo_ssh_dir", lambda: ssh_dir)
    from rodeo.ssh_key import ensure_rodeo_ssh_key

    ensure_rodeo_ssh_key()
    return ssh_dir


def test_parse_host_vm():
    assert parse_ssh_target_arg("student-01/rancher") == ("student-01", "rancher")
    assert parse_ssh_target_arg("rancher") == (None, "rancher")


def test_build_ssh_target_local_vm(managed_ssh):
    cfg = {
        "vms": {
            "rancher": {"ip": "192.168.122.10", "user": "root"},
        }
    }
    t = build_ssh_target("rancher", cfg=cfg)
    assert t.host == "192.168.122.10"
    assert t.user == "root"
    assert t.jump_host is None
    argv = ssh_argv_for(t)
    assert "root@192.168.122.10" in argv
    assert "ProxyJump" not in " ".join(argv)


def test_default_identity_prefers_root_key_when_readable_even_if_not_root(
    managed_ssh, tmp_path, monkeypatch
):
    """Nested VMs only trust the host's /root/.ssh/id_ed25519 (baked into their
    cloud-init) — prefer_root_key must pick it regardless of the invoking
    user's euid, not just when already running as root."""
    fake_root_key = tmp_path / "fake_root_id_ed25519"
    fake_root_key.write_text("fake-key")
    monkeypatch.setattr("rodeo.ssh_targets._HOST_ROOT_SSH_KEY", fake_root_key)
    monkeypatch.setattr("rodeo.ssh_targets.os.geteuid", lambda: 1000)  # not root
    result = default_identity({}, prefer_root_key=True)
    assert result == str(fake_root_key)


def test_default_identity_raises_clear_error_when_root_key_unreadable(
    managed_ssh, tmp_path, monkeypatch
):
    """Regression: previously this silently fell back to the operator's managed
    key (which the VM doesn't trust), degrading into an unexplained interactive
    password prompt. Must fail with an actionable sudo hint instead."""
    fake_root_key = tmp_path / "fake_root_id_ed25519"
    fake_root_key.write_text("fake-key")
    monkeypatch.setattr("rodeo.ssh_targets._HOST_ROOT_SSH_KEY", fake_root_key)
    monkeypatch.setattr("rodeo.ssh_targets.os.geteuid", lambda: 1000)
    monkeypatch.setattr("rodeo.ssh_targets.os.access", lambda *a, **k: False)
    with pytest.raises(ConfigError, match="sudo"):
        default_identity({}, prefer_root_key=True)


def test_default_identity_explicit_key_still_wins_over_root_key(
    managed_ssh, tmp_path, monkeypatch
):
    """--key / plan-level ssh.identity_file must still override, even for
    nested-VM targets — prefer_root_key only affects the final fallback."""
    fake_root_key = tmp_path / "fake_root_id_ed25519"
    fake_root_key.write_text("fake-key")
    monkeypatch.setattr("rodeo.ssh_targets._HOST_ROOT_SSH_KEY", fake_root_key)
    chosen = tmp_path / "explicit_key"
    chosen.write_text("explicit")
    result = default_identity({}, key=str(chosen), prefer_root_key=True)
    assert result == str(chosen)


def test_build_ssh_target_local_vm_uses_root_key_when_available(
    managed_ssh, tmp_path, monkeypatch
):
    """rodeo ssh <local-vm> must resolve the host's root key, not the operator's
    managed key — end-to-end through build_ssh_target, not just the helper."""
    fake_root_key = tmp_path / "fake_root_id_ed25519"
    fake_root_key.write_text("fake-key")
    monkeypatch.setattr("rodeo.ssh_targets._HOST_ROOT_SSH_KEY", fake_root_key)
    monkeypatch.setattr("rodeo.ssh_targets.os.geteuid", lambda: 1000)
    cfg = {"vms": {"rancher": {"ip": "192.168.122.9", "user": "root"}}}
    t = build_ssh_target("rancher", cfg=cfg)
    assert t.identity_file == str(fake_root_key)


def test_build_ssh_target_host_from_workshop(managed_ssh, tmp_path, monkeypatch):
    ws = tmp_path / "workshop.yaml"
    ws.write_text(
        textwrap.dedent(
            """
            name: demo
            lab:
              dir: /root/lab
            defaults:
              ssh_user: ec2-user
            hosts:
              - id: student-01
                ssh: 203.0.113.10
                public_ip: 203.0.113.10
            """
        )
    )
    monkeypatch.chdir(tmp_path)
    t = build_ssh_target("student-01", cfg={})
    assert t.host == "203.0.113.10"
    assert t.user == "ec2-user"
    assert t.jump_host is None


def test_build_ssh_target_host_vm_jump(managed_ssh, tmp_path, monkeypatch):
    ws = tmp_path / "workshop.yaml"
    ws.write_text(
        textwrap.dedent(
            """
            name: demo
            lab:
              dir: /root/lab
            defaults:
              ssh_user: ec2-user
            hosts:
              - id: student-01
                ssh: 203.0.113.10
                public_ip: 203.0.113.10
            """
        )
    )
    monkeypatch.chdir(tmp_path)
    cfg = {
        "vms": {
            "rancher": {"ip": "192.168.122.10", "user": "root"},
        }
    }
    t = build_ssh_target("student-01/rancher", cfg=cfg)
    assert t.host == "192.168.122.10"
    assert t.jump_host == "203.0.113.10"
    assert t.jump_user == "ec2-user"
    argv = ssh_argv_for(t)
    assert any(a.startswith("ProxyJump=") or a == "ProxyJump=ec2-user@203.0.113.10" for a in argv) or any(
        "ProxyJump=ec2-user@203.0.113.10" in a for a in argv
    )


def test_unknown_target(managed_ssh, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match="unknown target"):
        build_ssh_target("nope", cfg={})
