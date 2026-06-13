"""Host detection and profile-fit recommendation."""
from __future__ import annotations

from rodeo import preflight


def test_detect_host_has_expected_keys():
    host = preflight.detect_host()
    for key in (
        "is_root", "pkg_mgr", "has_kvm", "nested", "ram_total_gib",
        "ram_avail_gib", "cpus", "disk_free_gib", "core_tools", "optional_tools",
    ):
        assert key in host
    assert set(host["core_tools"]) == set(preflight.CORE_TOOLS)


def _host(ram_avail):
    return {"ram_avail_gib": ram_avail, "ram_total_gib": ram_avail}


def test_recommend_largest_that_fits():
    assert preflight.recommend_profile(_host(64)) == ("harvester", True)
    assert preflight.recommend_profile(_host(40)) == ("test", True)
    assert preflight.recommend_profile(_host(16)) == ("rancher", True)


def test_recommend_warns_when_nothing_fits():
    name, fits = preflight.recommend_profile(_host(4))
    assert name == "rancher"  # smallest
    assert fits is False


def test_missing_core_tools():
    host = {"core_tools": {"ansible-playbook": True, "kubectl": False}}
    assert preflight.missing_core_tools(host) == ["kubectl"]


def test_run_preflight_returns_bool(tmp_path, capsys):
    cfg = {
        "name": "t",
        "resources": {"harvester": {"memory_mib": 8192, "disk_gb": 50},
                      "rancher": {"memory_mib": 4096, "disk_gb": 30}},
        "storage": {"image_dir": str(tmp_path)},
    }
    result = preflight.run_preflight(cfg, tmp_path)
    assert isinstance(result, bool)
    out = capsys.readouterr().out
    assert "Preflight" in out
