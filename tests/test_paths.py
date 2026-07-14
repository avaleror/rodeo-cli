"""~/.rodeo paths resolve to the invoking user under sudo."""
from __future__ import annotations

import os
from pathlib import Path

from rodeo.paths import (
    fix_invoking_ownership,
    harvester_kubeconfig_path,
    invoking_home,
    rodeo_dir,
    rodeo_secrets_path,
    rodeo_state_dir,
)


def test_invoking_home_follows_sudo_user(tmp_path, monkeypatch):
    real_home = tmp_path / "userhome"
    real_home.mkdir()
    monkeypatch.setenv("HOME", "/root")
    monkeypatch.setenv("SUDO_USER", "alice")
    monkeypatch.setattr(
        "pwd.getpwnam",
        lambda name: type("Pw", (), {"pw_dir": str(real_home)})(),
    )
    assert invoking_home() == real_home


def test_rodeo_paths_under_invoking_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SUDO_USER", raising=False)
    assert rodeo_dir() == tmp_path / ".rodeo"
    assert rodeo_secrets_path() == tmp_path / ".rodeo" / "secrets.yaml"
    assert rodeo_state_dir() == tmp_path / ".rodeo" / "state"
    assert harvester_kubeconfig_path() == tmp_path / ".rodeo" / "harvester-kubeconfig"


def _fake_pwnam(pw_dir="/home/whoever", uid=1000, gid=1000):
    return lambda name: type("Pw", (), {"pw_dir": pw_dir, "pw_uid": uid, "pw_gid": gid})()


def test_fix_ownership_noop_without_sudo_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    calls = []
    monkeypatch.setattr(os, "chown", lambda *a: calls.append(a))
    fix_invoking_ownership()
    assert calls == []


def test_fix_ownership_noop_when_not_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SUDO_USER", "alice")
    monkeypatch.setattr(os, "geteuid", lambda: 1000)  # not root
    calls = []
    monkeypatch.setattr(os, "chown", lambda *a: calls.append(a))
    fix_invoking_ownership()
    assert calls == []


def test_fix_ownership_chowns_everything_under_rodeo_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SUDO_USER", "alice")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr("pwd.getpwnam", _fake_pwnam(pw_dir=str(tmp_path), uid=1000, gid=1000))

    rodeo_dir_path = tmp_path / ".rodeo"
    state_dir = rodeo_dir_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "plan.yaml").write_text("phases: {}")

    calls = []
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: calls.append((Path(path), uid, gid)))
    fix_invoking_ownership()

    chowned = {c[0] for c in calls}
    assert rodeo_dir_path in chowned
    assert state_dir in chowned
    assert (state_dir / "plan.yaml") in chowned
    assert all(uid == 1000 and gid == 1000 for _, uid, gid in calls)


def test_fix_ownership_survives_chown_errors(tmp_path, monkeypatch):
    """A permission error on one file must not stop the rest from being fixed."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SUDO_USER", "alice")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr("pwd.getpwnam", _fake_pwnam(pw_dir=str(tmp_path)))

    rodeo_dir_path = tmp_path / ".rodeo"
    rodeo_dir_path.mkdir()
    (rodeo_dir_path / "a").write_text("x")
    (rodeo_dir_path / "b").write_text("y")

    chowned = []

    def _flaky_chown(path, uid, gid):
        if str(path).endswith("/a"):
            raise OSError("nope")
        chowned.append(path)

    monkeypatch.setattr(os, "chown", _flaky_chown)
    fix_invoking_ownership()  # must not raise despite the error on "a"
    assert any(str(p).endswith("/b") for p in chowned)