"""ansible-galaxy collection install cache."""
from __future__ import annotations

from rodeo.galaxy_collections import (
    collections_install_needed,
    ensure_collections,
    mark_collections_installed,
    requirements_hash,
)
from rodeo.paths import rodeo_dir


def test_collections_install_needed_when_marker_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    req = tmp_path / "requirements.yml"
    req.write_text("collections:\n  - name: community.general\n")
    assert collections_install_needed(req) is True


def test_collections_install_skipped_when_hash_matches(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    req = tmp_path / "requirements.yml"
    req.write_text("collections:\n  - name: community.general\n")
    mark_collections_installed(req)
    assert collections_install_needed(req) is False


def test_collections_install_runs_when_requirements_change(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    req = tmp_path / "requirements.yml"
    req.write_text("collections:\n  - name: community.general\n")
    mark_collections_installed(req)
    req.write_text("collections:\n  - name: ansible.posix\n")
    assert collections_install_needed(req) is True


def test_ensure_collections_skips_galaxy_when_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    req = tmp_path / "requirements.yml"
    req.write_text("collections:\n  - name: community.general\n")
    mark_collections_installed(req)

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        class _Result:
            returncode = 0
            stderr = ""
        return _Result()

    monkeypatch.setattr("rodeo.galaxy_collections.subprocess.run", _fake_run)
    ran, result = ensure_collections(req)
    assert ran is False
    assert result is None
    assert calls == []


def test_ensure_collections_writes_marker_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    req = tmp_path / "requirements.yml"
    req.write_text("collections:\n  - name: community.general\n")

    def _fake_run(cmd, **kwargs):
        class _Result:
            returncode = 0
            stderr = ""
        return _Result()

    monkeypatch.setattr("rodeo.galaxy_collections.subprocess.run", _fake_run)
    ran, result = ensure_collections(req)
    assert ran is True
    assert result is not None and result.returncode == 0
    marker = rodeo_dir() / "ansible-collections.sha256"
    assert marker.read_text().strip() == requirements_hash(req)