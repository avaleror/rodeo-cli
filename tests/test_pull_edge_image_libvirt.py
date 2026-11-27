"""pull-edge-image for a libvirt-group user (the lab player's student): the
image streams from the eib VM straight into the libvirt pool, never sudo and
never through the user's home (the small root disk on fleet hosts)."""
from __future__ import annotations

import subprocess

import pytest

from rodeo.commands import pull_edge_image_cmd as pe

DOMXML = ("<domain><devices><disk type='file' device='disk'>"
          "<source file='/var/lib/libvirt/images/{n}-vda.qcow2'/><target dev='vda'/></disk>"
          "</devices></domain>")


class _Reader:
    stdout = None

    def __init__(self, cmd, **kw):
        self.cmd = cmd

    def wait(self):
        return 0


@pytest.fixture
def calls(monkeypatch):
    seen: list[list[str]] = []

    def run(cmd, **kw):
        seen.append(list(cmd))
        out = ""
        if cmd[0] == "ssh" and "stat" in cmd:
            out = "32212254720\n"
        elif cmd[:1] == ["virsh"]:
            verb = cmd[3]
            if verb == "dumpxml":
                out = DOMXML.format(n=cmd[-1])
            elif verb == "vol-pool":
                out = "default\n"
            elif verb == "vol-path":
                out = f"/var/lib/libvirt/images/{cmd[-1]}\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def popen(cmd, **kw):
        seen.append(list(cmd))
        return _Reader(cmd)

    monkeypatch.setattr(pe.subprocess, "run", run)
    monkeypatch.setattr(pe.subprocess, "Popen", popen)
    return seen


def test_raw_image_streams_into_the_pool_and_nodes_get_overlays(calls):
    pe._libvirt_pull("192.168.122.20", "/home/eib-standalone/rke2-edge3.raw", ["edge3"],
                     "qemu:///system", 25, "/home/student/.ssh/id_ed25519")
    flat = [" ".join(c) for c in calls]
    assert any(c.endswith("root@192.168.122.20 cat /home/eib-standalone/rke2-edge3.raw") for c in flat)
    assert any("vol-upload --pool default rke2-edge3-base.raw /dev/stdin" in c for c in flat)
    clone = next(c for c in flat if "vol-create-as default edge3-vda.qcow2" in c)
    assert "32212254720" in clone
    assert "--backing-vol /var/lib/libvirt/images/rke2-edge3-base.raw --backing-vol-format raw" in clone
    # No sudo, no scp into a local work dir, no qemu-img on the image dir.
    assert not any(c.startswith(("sudo", "scp", "qemu-img")) for c in flat)


def test_iso_gets_a_blank_disk_and_a_cdrom(calls, monkeypatch):
    monkeypatch.setattr(pe.Path, "exists", lambda self: True)
    pe._libvirt_pull("192.168.122.20", "/home/eib-workspace/elemental-edge1.iso", ["edge1"],
                     "qemu:///system", 25, "k")
    flat = [" ".join(c) for c in calls]
    assert any("vol-upload --pool default elemental-edge1.iso /dev/stdin" in c for c in flat)
    assert any("vol-create-as default edge1-vda.qcow2 25G --format qcow2" in c for c in flat)
    assert any(c.startswith("virsh -c qemu:///system define") for c in flat)


def test_root_is_never_a_libvirt_user(monkeypatch):
    monkeypatch.setattr(pe, "is_root", lambda: True)
    assert pe.libvirt_user() is False
