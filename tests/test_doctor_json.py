"""Tests for ``rodeo.service.doctor.doctor_report``."""
from __future__ import annotations

from rodeo.service.doctor import doctor_report


def test_doctor_report_shape(monkeypatch):
    host = {
        "is_root": True,
        "pkg_mgr": "zypper",
        "has_kvm": True,
        "nested": True,
        "ram_total_gib": 96,
        "ram_avail_gib": 90,
        "cpus": 32,
        "image_dir": "/var/lib/libvirt/images",
        "disk_free_gib": 800,
        "core_tools": {"ansible-playbook": True, "ansible-galaxy": True, "kubectl": True},
        "optional_tools": {"virsh": True, "ssh": True},
        "py_modules": {"libvirt": True, "lxml": True},
    }
    monkeypatch.setattr("rodeo.service.doctor.detect_host", lambda *a, **k: host)
    monkeypatch.setattr(
        "rodeo.service.doctor.recommend_profile",
        lambda h: ("harvester", True),
    )

    report = doctor_report()
    assert report["recommended_profile"] == "harvester"
    assert report["profile_fits"] is True
    assert report["host"]["cpus"] == 32
    assert report["host"]["has_kvm"] is True
    assert report["core_tools"]["kubectl"] is True
    assert report["py_modules"]["libvirt"] is True
    assert report["optional_tools"]["virsh"] is True


def test_doctor_cmd_json(monkeypatch):
    from click.testing import CliRunner

    from rodeo.commands.doctor_cmd import doctor_cmd

    monkeypatch.setattr(
        "rodeo.commands.doctor_cmd.doctor_report",
        lambda: {
            "host": {"cpus": 8},
            "core_tools": {},
            "py_modules": {},
            "optional_tools": {},
            "recommended_profile": "rancher",
            "profile_fits": True,
        },
    )
    result = CliRunner().invoke(doctor_cmd, ["--output", "json"])
    assert result.exit_code == 0
    assert '"recommended_profile": "rancher"' in result.output
