"""clean must delete every rodeo artifact type, and no non-rodeo VM disk.

Regression guard: OVMF var stores (*-ovmf-vars.fd) and cloud-init seed ISOs
(rancher/eib) used to leak because the delete patterns only matched
*_vars.bin and harvester-config-*.iso. This pins the coverage.
"""
from __future__ import annotations

import fnmatch
import inspect
import re

from rodeo.commands import clean as clean_mod


def _patterns():
    """Extract every quoted glob from clean_cmd's `patterns = [...]` literal."""
    src = inspect.getsource(clean_mod.clean_cmd.callback)
    start = src.index("patterns = [")
    body = src[start:src.index("]", start)]
    return re.findall(r'"([^"]+)"', body)


ARTIFACTS = [
    # disks, every node type
    "harvester1-vda.qcow2", "rancher-vda.qcow2", "eib-vda.qcow2", "edge3-vda.qcow2",
    # OVMF UEFI var stores (the leak this guards)
    "harvester2-ovmf-vars.fd", "rancher-ovmf-vars.fd", "eib-ovmf-vars.fd", "edge1-ovmf-vars.fd",
    # config + cloud-init seed ISOs (rancher/eib cloud-init were leaking)
    "harvester-config-node1.iso", "rancher-cloud-init.iso", "eib-cloud-init.iso",
    # base images
    "harvester-v1.8.1-amd64.iso", "Leap-16.0-Cloud.qcow2", "Leap-Micro-6.2.qcow2",
    "SL-Micro.x86_64-6.2-Default.raw",
    "openSUSE-Leap-Micro.x86_64-Default-SelfInstall.iso",
    "openSUSE-Leap-Micro.x86_64-Default.raw", "openSUSE-Leap-Micro.x86_64-Default.raw.xz",
    # interrupted-transfer temp files from a failed run
    "Leap-16.0-Cloud.qcow2.downloading", "rancher-vda.qcow2.building",
]

# A non-rodeo VM sharing the libvirt pool must survive a clean.
NON_RODEO = ["my-personal-vm.qcow2", "ubuntu-server-vda.qcow2", "fedora-workstation.qcow2"]


def test_all_rodeo_artifacts_covered():
    pats = _patterns()
    for name in ARTIFACTS:
        assert any(fnmatch.fnmatch(name, p) for p in pats), f"clean would leak {name}"


def test_non_rodeo_disks_untouched():
    pats = _patterns()
    for name in NON_RODEO:
        assert not any(fnmatch.fnmatch(name, p) for p in pats), f"clean would wrongly delete {name}"
