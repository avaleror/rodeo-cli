"""install-deps: grant the invoking (non-root) user unprivileged libvirt access."""
from __future__ import annotations

import subprocess

from rodeo.commands import install_deps as mod


def _cp(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout)


def test_noop_when_no_sudo_user(monkeypatch):
    """Genuinely root, no invoking user — nothing to grant access to."""
    monkeypatch.delenv("SUDO_USER", raising=False)
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: calls.append(a) or _cp())
    mod._ensure_invoking_user_in_libvirt_group()
    assert calls == []


def test_noop_when_sudo_user_is_root(monkeypatch):
    monkeypatch.setenv("SUDO_USER", "root")
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: calls.append(a) or _cp())
    mod._ensure_invoking_user_in_libvirt_group()
    assert calls == []


def test_noop_when_no_libvirt_group_on_host(monkeypatch):
    monkeypatch.setenv("SUDO_USER", "alice")
    calls = []

    def _run(cmd, **k):
        calls.append(cmd)
        if cmd[:2] == ["getent", "group"]:
            return _cp(returncode=1)  # group doesn't exist
        return _cp()

    monkeypatch.setattr(mod.subprocess, "run", _run)
    mod._ensure_invoking_user_in_libvirt_group()
    # Only the getent probe ran — never tried id/usermod.
    assert calls == [["getent", "group", "libvirt"]]


def test_skips_usermod_when_already_a_member(monkeypatch):
    monkeypatch.setenv("SUDO_USER", "alice")
    calls = []

    def _run(cmd, **k):
        calls.append(cmd)
        if cmd[:2] == ["getent", "group"]:
            return _cp(returncode=0)
        if cmd[:2] == ["id", "-nG"]:
            return _cp(stdout="alice wheel libvirt\n")
        return _cp()

    monkeypatch.setattr(mod.subprocess, "run", _run)
    mod._ensure_invoking_user_in_libvirt_group()
    assert not any(c[:2] == ["usermod", "-aG"] for c in calls)


def test_adds_user_to_libvirt_group_when_missing(monkeypatch):
    monkeypatch.setenv("SUDO_USER", "alice")
    calls = []

    def _run(cmd, **k):
        calls.append(cmd)
        if cmd[:2] == ["getent", "group"]:
            return _cp(returncode=0)
        if cmd[:2] == ["id", "-nG"]:
            return _cp(stdout="alice wheel\n")
        return _cp()

    monkeypatch.setattr(mod.subprocess, "run", _run)
    mod._ensure_invoking_user_in_libvirt_group()
    assert ["usermod", "-aG", "libvirt", "alice"] in calls
