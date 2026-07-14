"""stream_boot: edge nodes must be skipped, not crash the whole boot phase."""
from __future__ import annotations

import pytest

from rodeo.engine import libvirt as libvirt_mod
from rodeo.engine.libvirt import VMInfo
from rodeo.engine.runner import DeployRunner, LogLine


class FakeLibvirtDriver:
    """Records start() calls; get_vm() returns from a name->VMInfo map."""

    def __init__(self, uri: str = "qemu:///system") -> None:
        self.uri = uri
        self.started: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def net_start(self, name: str = "default") -> None:
        pass

    def net_set_autostart(self, name: str = "default", enabled: bool = True) -> None:
        pass

    def get_vm(self, name: str) -> VMInfo:
        return FakeLibvirtDriver.VM_STATES.get(name, VMInfo(name=name, state="not found"))

    def start(self, name: str) -> None:
        if name in FakeLibvirtDriver.START_FAILS:
            raise RuntimeError(f"boom starting {name}")
        self.started.append(name)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    cfg = {
        "type": "suse-edge",
        "name": "edge-test",
        "vms": {"rancher": {}, "eib": {}, "edge1": {}, "edge2": {}},
        "storage": {"image_dir": str(tmp_path)},
        "deployment_target": "baremetal",
    }
    r = DeployRunner(cfg=cfg, root=tmp_path)
    monkeypatch.setattr(DeployRunner, "_start_firewalld", lambda self: iter(()))
    monkeypatch.setattr(libvirt_mod, "LibvirtDriver", FakeLibvirtDriver)
    FakeLibvirtDriver.VM_STATES = {
        "rancher": VMInfo(name="rancher", state="shut off"),
        "eib": VMInfo(name="eib", state="shut off"),
        "edge1": VMInfo(name="edge1", state="shut off"),
        "edge2": VMInfo(name="edge2", state="shut off"),
    }
    FakeLibvirtDriver.START_FAILS = set()
    return r


def _drain(gen):
    events = []
    for event in gen:
        events.append(event)
    return events


def test_edge_nodes_without_disk_are_skipped_not_fatal(runner):
    """No edgeN-vda.qcow2 on disk (pull-edge-image never ran) — must skip, not crash."""
    events = _drain(runner.stream_boot())
    assert runner._last_rc == 0
    lines = [e.line for e in events if isinstance(e, LogLine)]
    assert any("edge1" in line and "skipping" in line for line in lines)
    assert any("edge2" in line and "skipping" in line for line in lines)


def test_non_edge_vms_still_start_normally(runner):
    events = _drain(runner.stream_boot())
    assert runner._last_rc == 0
    lines = [e.line for e in events if isinstance(e, LogLine)]
    assert any("rancher: started." in line for line in lines)
    assert any("eib: started." in line for line in lines)


def test_edge_node_with_disk_present_is_started(runner, tmp_path):
    """Once rodeo pull-edge-image seeded the disk, the edge node boots like any VM."""
    (tmp_path / "edge1-vda.qcow2").write_bytes(b"fake qcow2")
    events = _drain(runner.stream_boot())
    assert runner._last_rc == 0
    lines = [e.line for e in events if isinstance(e, LogLine)]
    assert any("edge1: started." in line for line in lines)
    # edge2 still has no disk — still skipped.
    assert any("edge2" in line and "skipping" in line for line in lines)


def test_missing_non_edge_domain_is_still_fatal(runner):
    """A real VM (not an edge node) missing entirely must still abort the phase."""
    FakeLibvirtDriver.VM_STATES["rancher"] = VMInfo(name="rancher", state="not found")
    events = _drain(runner.stream_boot())
    assert runner._last_rc == 1
    lines = [e.line for e in events if isinstance(e, LogLine)]
    assert any("rancher" in line and "domain not found" in line for line in lines)


def test_start_failure_on_a_real_vm_is_fatal_but_does_not_crash(runner):
    FakeLibvirtDriver.START_FAILS = {"rancher"}
    events = _drain(runner.stream_boot())
    assert runner._last_rc == 1
    lines = [e.line for e in events if isinstance(e, LogLine)]
    assert any("rancher" in line and "failed to start" in line for line in lines)
