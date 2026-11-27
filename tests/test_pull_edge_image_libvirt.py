"""pull-edge-image for a libvirt-group user (the lab player's student): every
write to the image dir goes through libvirt, never sudo."""
from __future__ import annotations

import json
import subprocess

import pytest

from rodeo.commands import pull_edge_image_cmd as pe

DOMXML = ("<domain><devices><disk type='file' device='disk'>"
          "<source file='/var/lib/libvirt/images/{n}-vda.qcow2'/><target dev='vda'/></disk>"
          "</devices></domain>")


@pytest.fixture
def calls(monkeypatch, tmp_path):
    seen: list[list[str]] = []
    monkeypatch.setattr(pe.Path, "home", lambda: tmp_path)

    def run(cmd, **kw):
        seen.append(list(cmd))
        out = ""
        if cmd[0] == "scp":
            open(cmd[-1], "wb").write(b"raw")
        elif cmd[0] == "qemu-img" and cmd[1] == "convert":
            open(cmd[-1], "wb").write(b"qcow2")
        elif cmd[0] == "qemu-img" and cmd[1] == "info":
            out = json.dumps({"virtual-size": 32212254720})
        elif cmd[:1] == ["virsh"]:
            verb = cmd[3]
            if verb == "dumpxml":
                out = DOMXML.format(n=cmd[-1])
            elif verb == "vol-pool":
                out = "default\n"
            elif verb == "vol-path":
                out = f"/var/lib/libvirt/images/{cmd[-1]}\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(pe.subprocess, "run", run)
    return seen


def test_raw_image_becomes_base_and_thin_clones_through_libvirt(calls, tmp_path):
    pe._libvirt_pull("192.168.122.20", "/home/eib-standalone/rke2-edge3.raw", ["edge3"],
                     "qemu:///system", 25, str(tmp_path / ".ssh/id_ed25519"))
    flat = [" ".join(c) for c in calls]
    assert any(c.startswith("scp -i") and "root@192.168.122.20:/home/eib-standalone/rke2-edge3.raw" in c
               for c in flat)
    assert any("vol-upload --pool default rke2-edge3-base.qcow2" in c for c in flat)
    clone = next(c for c in flat if "vol-create-as default edge3-vda.qcow2" in c)
    assert "32212254720" in clone and "--backing-vol /var/lib/libvirt/images/rke2-edge3-base.qcow2" in clone
    # Nothing writes the image dir directly or asks for root.
    assert not any(c.startswith("sudo") for c in flat)
    assert not any(c.startswith("qemu-img") and "/var/lib/libvirt" in c for c in flat)
    assert not list(tmp_path.glob("rodeo-edge-*"))  # the work dir is gone


def test_iso_gets_a_blank_disk_and_a_cdrom(calls, tmp_path, monkeypatch):
    monkeypatch.setattr(pe.Path, "exists", lambda self: True)
    pe._libvirt_pull("192.168.122.20", "/home/eib-workspace/elemental-edge1.iso", ["edge1"],
                     "qemu:///system", 25, "k")
    flat = [" ".join(c) for c in calls]
    assert any("vol-upload --pool default elemental-edge1.iso" in c for c in flat)
    assert any("vol-create-as default edge1-vda.qcow2 25G --format qcow2" in c for c in flat)
    assert any(c.startswith("virsh -c qemu:///system define") for c in flat)


def test_root_is_never_a_libvirt_user(monkeypatch):
    monkeypatch.setattr(pe, "is_root", lambda: True)
    assert pe.libvirt_user() is False
