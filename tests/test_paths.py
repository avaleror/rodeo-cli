"""~/.rodeo paths resolve to the invoking user under sudo."""
from __future__ import annotations

from rodeo.paths import (
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