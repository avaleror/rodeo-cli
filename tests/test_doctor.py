"""rodeo doctor — host report (detection mocked)."""
from __future__ import annotations

from click.testing import CliRunner

from rodeo.commands import doctor_cmd as doc_mod


def _host(ram):
    return {
        "is_root": False, "pkg_mgr": "apt", "has_kvm": True, "nested": True,
        "ram_total_gib": ram, "ram_avail_gib": ram, "cpus": 16,
        "image_dir": "/var/lib/libvirt/images", "disk_free_gib": 500,
        "core_tools": {"ansible-playbook": True, "ansible-galaxy": True, "kubectl": True},
        "optional_tools": {"virsh": True, "ssh": True},
    }


def test_doctor_recommends_when_fits(monkeypatch):
    monkeypatch.setattr(doc_mod, "detect_host", lambda *a, **k: _host(64))
    result = CliRunner().invoke(doc_mod.doctor_cmd, [])
    assert result.exit_code == 0, result.output
    assert "Recommended" in result.output
    assert "harvester" in result.output
    assert "Instruqt tip" in result.output
    assert "guest vCPU" in result.output


def test_doctor_warns_when_too_small(monkeypatch):
    monkeypatch.setattr(doc_mod, "detect_host", lambda *a, **k: _host(8))
    result = CliRunner().invoke(doc_mod.doctor_cmd, [])
    assert result.exit_code == 0, result.output
    assert "No profile fully fits" in result.output
