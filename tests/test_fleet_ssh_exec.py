"""Tests for the laptop -> KVM-host OpenSSH argv builder."""
from __future__ import annotations

from rodeo.fleet.inventory import FleetHost, FleetInventory
from rodeo.fleet.ssh_exec import ssh_argv


def _inv(**defaults) -> FleetInventory:
    return FleetInventory(name="demo", lab_dir="/root/lab", defaults=defaults, hosts=[])


def test_ssh_argv_trusts_new_hosts_but_pins_known_ones(tmp_path, monkeypatch):
    """Workshop hosts are freshly provisioned and unknown to the laptop's
    known_hosts; BatchMode=yes alone would fail the first connection to every
    host with "Host key verification failed". accept-new keeps that working,
    and (security review 2026-09-30) a per-workshop known_hosts then pins the
    key, where the old StrictHostKeyChecking=no + /dev/null trusted anything."""
    monkeypatch.setattr("rodeo.paths.rodeo_dir", lambda: tmp_path)
    argv = ssh_argv(_inv(), FleetHost(id="h1", ssh="10.0.0.1"), "rodeo doctor")
    assert "StrictHostKeyChecking=accept-new" in argv
    assert f"UserKnownHostsFile={tmp_path}/fleet/demo/known_hosts" in argv
    assert "StrictHostKeyChecking=no" not in argv
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


def test_ssh_argv_falls_back_to_managed_key(tmp_path, monkeypatch):
    """Provisioned hosts only trust ~/.rodeo/ssh/id_ed25519; fleet must use it
    without the operator setting defaults.identity_file (live bug 2026-09-30)."""
    key = tmp_path / "id_ed25519"
    key.write_text("k")
    monkeypatch.setattr("rodeo.ssh_key.rodeo_ssh_private_key_path", lambda: key)
    argv = ssh_argv(_inv(), FleetHost(id="h1", ssh="10.0.0.1"), "cmd")
    assert argv[argv.index("-i") + 1] == str(key)


def test_ssh_argv_explicit_identity_wins_over_managed_key(tmp_path, monkeypatch):
    key = tmp_path / "id_ed25519"
    key.write_text("k")
    monkeypatch.setattr("rodeo.ssh_key.rodeo_ssh_private_key_path", lambda: key)
    argv = ssh_argv(_inv(identity_file="/keys/byo"), FleetHost(id="h1", ssh="10.0.0.1"), "cmd")
    assert argv[argv.index("-i") + 1] == "/keys/byo"
    assert argv.count("-i") == 1


def test_ssh_argv_no_identity_when_managed_key_missing(tmp_path, monkeypatch):
    """Read-only commands must not create a key as a side effect."""
    monkeypatch.setattr("rodeo.ssh_key.rodeo_ssh_private_key_path", lambda: tmp_path / "absent")
    argv = ssh_argv(_inv(), FleetHost(id="h1", ssh="10.0.0.1"), "cmd")
    assert "-i" not in argv
    assert not (tmp_path / "absent").exists()


def _captured_remote(monkeypatch):
    seen = {}

    class P:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return P()

    monkeypatch.setattr("rodeo.fleet.ssh_exec.subprocess.run", fake_run)
    return seen


def test_run_remote_wraps_non_root_user_in_sudo(monkeypatch):
    """ssh_user: ec2-user must still act on root's ~/.rodeo and /root/lab
    (live bug 2026-09-30: deploy failed with 'Root privileges are required')."""
    from rodeo.fleet.ssh_exec import run_remote

    seen = _captured_remote(monkeypatch)
    run_remote(_inv(ssh_user="ec2-user"), FleetHost(id="h1", ssh="10.0.0.1"), ["rodeo", "status"])
    assert seen["cmd"][-1] == "sudo -n -H bash -lc 'rodeo status'"


def test_run_remote_root_user_is_not_wrapped(monkeypatch):
    from rodeo.fleet.ssh_exec import run_remote

    seen = _captured_remote(monkeypatch)
    run_remote(_inv(ssh_user="ec2-user"), FleetHost(id="h1", ssh="root@10.0.0.1"), ["rodeo", "status"])
    assert seen["cmd"][-1] == "rodeo status"


def test_run_remote_as_root_false_runs_as_login_user(monkeypatch):
    from rodeo.fleet.ssh_exec import run_remote

    seen = _captured_remote(monkeypatch)
    run_remote(_inv(ssh_user="ec2-user"), FleetHost(id="h1", ssh="10.0.0.1"), ["sudo", "-n", "true"], as_root=False)
    assert seen["cmd"][-1] == "sudo -n true"


def test_known_hosts_path_is_sanitised(tmp_path, monkeypatch):
    from rodeo.fleet.ssh_exec import known_hosts_path

    monkeypatch.setattr("rodeo.paths.rodeo_dir", lambda: tmp_path)
    assert known_hosts_path("../../etc x").parent == tmp_path / "fleet" / "etc-x"


def test_forget_host_key_removes_only_that_address(tmp_path, monkeypatch):
    import subprocess

    from rodeo.fleet.ssh_exec import forget_host_key, known_hosts_path

    monkeypatch.setattr("rodeo.paths.rodeo_dir", lambda: tmp_path)
    kh = known_hosts_path("demo")
    kh.parent.mkdir(parents=True)
    key = tmp_path / "k"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    pub = (tmp_path / "k.pub").read_text().split()[:2]
    kh.write_text(f"10.0.0.1 {' '.join(pub)}\n10.0.0.2 {' '.join(pub)}\n")
    forget_host_key("demo", "10.0.0.1")
    text = kh.read_text()
    assert "10.0.0.1" not in text and "10.0.0.2" in text
    assert not (tmp_path / "fleet" / "demo" / "known_hosts.old").exists()
