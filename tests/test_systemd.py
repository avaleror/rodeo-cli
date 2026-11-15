"""The systemd units rodeo installs (rodeo/systemd.py): which scope they live in,
and restarting one only when this host really runs it."""
from __future__ import annotations

import subprocess

from rodeo import systemd


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _usable(monkeypatch, *, root: bool) -> None:
    """A host where systemd is reachable, as root or as a user."""
    monkeypatch.setattr(systemd.sys, "platform", "linux")
    monkeypatch.setattr(systemd.Path, "is_dir", lambda self: str(self) == "/run/systemd/system")
    monkeypatch.setattr(systemd.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(systemd.os, "geteuid", lambda: 0 if root else 1000)


def test_root_uses_the_system_scope_and_a_user_the_user_scope(monkeypatch):
    _usable(monkeypatch, root=True)
    assert systemd.systemctl() == ["systemctl"]
    _usable(monkeypatch, root=False)
    assert systemd.systemctl() == ["systemctl", "--user"]


def test_no_systemctl_no_scope(monkeypatch):
    _usable(monkeypatch, root=True)
    monkeypatch.setattr(systemd.shutil, "which", lambda name: None)
    assert systemd.systemctl() is None
    assert systemd.unit_is_active() is False
    assert systemd.restart_unit() == (False, "no systemd in this scope")


def test_not_linux_no_scope(monkeypatch):
    _usable(monkeypatch, root=True)
    monkeypatch.setattr(systemd.sys, "platform", "darwin")
    assert systemd.systemctl() is None


def test_restart_only_touches_a_running_unit(monkeypatch):
    _usable(monkeypatch, root=True)
    ran = []

    def fake_run(cmd, **kwargs):
        ran.append(cmd)
        return _cp(returncode=3) if cmd[1] == "is-active" else _cp()

    monkeypatch.setattr(systemd.subprocess, "run", fake_run)
    ok, why = systemd.restart_unit()
    assert ok is False and "not running" in why
    assert [c[1] for c in ran] == ["is-active"]


def test_restart_runs_in_the_right_scope(monkeypatch):
    _usable(monkeypatch, root=False)
    ran = []

    def fake_run(cmd, **kwargs):
        ran.append(cmd)
        return _cp()

    monkeypatch.setattr(systemd.subprocess, "run", fake_run)
    assert systemd.restart_unit() == (True, "")
    assert ran == [["systemctl", "--user", "is-active", "--quiet", systemd.BUILDER_UNIT],
                   ["systemctl", "--user", "restart", systemd.BUILDER_UNIT]]


def test_a_refused_restart_reports_why(monkeypatch):
    _usable(monkeypatch, root=True)

    def fake_run(cmd, **kwargs):
        return _cp() if cmd[1] == "is-active" else _cp(returncode=1, stderr="Job for rodeo-builder.service failed")

    monkeypatch.setattr(systemd.subprocess, "run", fake_run)
    ok, why = systemd.restart_unit()
    assert ok is False and "Job for rodeo-builder.service failed" in why


def test_a_hung_systemctl_is_not_fatal(monkeypatch):
    _usable(monkeypatch, root=True)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 120)

    monkeypatch.setattr(systemd.subprocess, "run", fake_run)
    assert systemd.unit_is_active() is False
    assert systemd.restart_unit()[0] is False
