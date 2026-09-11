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


def test_in_libvirt_group_true_when_gid_present(monkeypatch):
    monkeypatch.setattr(
        "grp.getgrnam", lambda name: type("Grp", (), {"gr_gid": 108})()
    )
    monkeypatch.setattr(preflight.os, "getgroups", lambda: [1000, 108])
    assert preflight._in_libvirt_group() is True


def test_in_libvirt_group_false_when_gid_absent(monkeypatch):
    monkeypatch.setattr(
        "grp.getgrnam", lambda name: type("Grp", (), {"gr_gid": 108})()
    )
    monkeypatch.setattr(preflight.os, "getgroups", lambda: [1000])
    assert preflight._in_libvirt_group() is False


def test_in_libvirt_group_true_when_no_libvirt_group_on_host(monkeypatch):
    import grp as grp_mod

    def _raise(name):
        raise KeyError(name)

    monkeypatch.setattr(grp_mod, "getgrnam", _raise)
    assert preflight._in_libvirt_group() is True


def test_run_preflight_warns_non_root_user_not_in_libvirt_group(tmp_path, monkeypatch, capsys):
    """Optional/warning check: must render with a ⚠, never a ✗ (that would fail the run)."""
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(preflight, "_in_libvirt_group", lambda: False)
    cfg = {"name": "t", "storage": {"image_dir": str(tmp_path)}}
    preflight.run_preflight(cfg, tmp_path, phases_to_run=["rancher"])
    out = capsys.readouterr().out
    libvirt_lines = [line for line in out.splitlines() if "libvirt group" in line]
    assert len(libvirt_lines) == 1
    assert "⚠" in libvirt_lines[0]


def test_run_preflight_skips_libvirt_group_check_for_root(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0)
    cfg = {"name": "t", "storage": {"image_dir": str(tmp_path)}}
    preflight.run_preflight(cfg, tmp_path, phases_to_run=["rancher"])
    out = capsys.readouterr().out
    assert "libvirt group" not in out


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


def _fake_block(tmp_path, devices, mounts):
    """Build a fake /sys/block + /proc/mounts pair."""
    sys_block = tmp_path / "sys-block"
    sys_block.mkdir(parents=True)
    for name, sectors, parts in devices:
        dev = sys_block / name
        dev.mkdir()
        (dev / "size").write_text(f"{sectors}\n")
        for part in parts:
            (dev / part).mkdir()
    proc_mounts = tmp_path / "mounts"
    proc_mounts.write_text("".join(f"{m} / ext4 rw 0 0\n" for m in mounts))
    return sys_block, proc_mounts


def test_pending_nvme_pool_reports_the_unmounted_instance_store(tmp_path):
    """The aws case: a 10 GB root EBS plus an idle 3.4 TB instance store.

    Regression: preflight measured only the mounted root volume and failed the
    deploy — "need ~2420 GB, have 7 GB free" — although kvm_host mounts the
    NVMe pool before any VM disk is written.
    """
    from rodeo.preflight import _pending_nvme_pool_gib

    sys_block, proc_mounts = _fake_block(
        tmp_path,
        devices=[("nvme0n1", 20971520, ["nvme0n1p1", "nvme0n1p3"]),   # 10 GiB root
                 ("nvme1n1", 7325302784, [])],                        # ~3.4 TiB store
        mounts=["/dev/nvme0n1p3"],
    )
    got = _pending_nvme_pool_gib(
        {"backend": "nvme"}, sys_block=sys_block, proc_mounts=proc_mounts
    )
    assert got > 3000


def test_pending_nvme_pool_skips_mounted_and_non_nvme_backends(tmp_path):
    from rodeo.preflight import _pending_nvme_pool_gib

    sys_block, proc_mounts = _fake_block(
        tmp_path,
        devices=[("nvme1n1", 7325302784, [])],
        mounts=["/dev/nvme1n1"],  # already mounted -> ordinary check applies
    )
    assert _pending_nvme_pool_gib(
        {"backend": "nvme"}, sys_block=sys_block, proc_mounts=proc_mounts
    ) == 0
    # Not an nvme-backed plan: never consulted.
    sys_block2, proc_mounts2 = _fake_block(
        tmp_path / "b", devices=[("nvme1n1", 7325302784, [])], mounts=[]
    )
    assert _pending_nvme_pool_gib(
        {}, sys_block=sys_block2, proc_mounts=proc_mounts2
    ) == 0
