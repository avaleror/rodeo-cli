"""Host detection and profile-fit recommendation."""
from __future__ import annotations

from rodeo import preflight
from rodeo import state


def test_detect_host_has_expected_keys():
    host = preflight.detect_host()
    for key in (
        "is_root", "pkg_mgr", "has_kvm", "nested", "ram_total_gib",
        "ram_avail_gib", "cpus", "disk_free_gib", "core_tools", "optional_tools",
        "py_modules",
    ):
        assert key in host
    assert set(host["core_tools"]) == set(preflight.CORE_TOOLS)
    assert set(host["py_modules"]) == set(preflight.CORE_PY_MODULES)


def _host(ram_avail):
    return {"ram_avail_gib": ram_avail, "ram_total_gib": ram_avail}


def test_recommend_largest_that_fits():
    assert preflight.recommend_profile(_host(80)) == ("harvester", True)
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


def _starved_cfg(tmp_path):
    return {
        "name": "starved-plan",
        # Impossibly large ask so the RAM/disk checks fail whenever they run.
        "resources": {"harvester": {"memory_mib": 999_999_999, "disk_gb": 999_999},
                      "rancher": {"memory_mib": 999_999_999, "disk_gb": 999_999}},
        "storage": {"image_dir": str(tmp_path)},
    }


def test_resource_checks_apply_on_fresh_deploy(tmp_path, capsys):
    """No prior state (vms never completed) — the full RAM/disk ask must be checked."""
    cfg = _starved_cfg(tmp_path)
    ok = preflight.run_preflight(cfg, tmp_path)
    assert ok is False
    assert "RAM" in capsys.readouterr().out


def test_resource_checks_skipped_when_vms_already_deployed(tmp_path, capsys):
    """A re-run against an already-deployed lab must not re-check fresh-provisioning RAM.

    Doesn't assert overall ``ok`` — unrelated host checks (root/kvm/nested virt/libvirt
    module) fail in this sandbox regardless of the resource-check change under test.
    """
    cfg = _starved_cfg(tmp_path)
    state.mark_phase_done("vms", cfg["name"])
    preflight.run_preflight(cfg, tmp_path, phases_to_run=["vms", "cluster"])
    out = capsys.readouterr().out
    assert "RAM" not in out
    assert "disk" not in out


def test_resource_checks_reapply_after_clean_resets_state(tmp_path, capsys):
    """Once vms is reset (e.g. by clean), the resource ask must be enforced again."""
    cfg = _starved_cfg(tmp_path)
    state.mark_phase_done("vms", cfg["name"])
    state.reset_phase("vms", cfg["name"])
    ok = preflight.run_preflight(cfg, tmp_path, phases_to_run=["vms", "cluster"])
    assert ok is False
    assert "RAM" in capsys.readouterr().out
