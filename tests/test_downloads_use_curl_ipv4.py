"""Large/binary downloads must use curl -4, not Ansible get_url.

get_url uses Python urllib which prefers IPv6 when the remote has AAAA
records — broken on Instruqt (errno 101). The rancher/eib image tasks already
use curl -4; these guards pin the same pattern on Harvester ISO + PXE artifacts.
"""
from __future__ import annotations

from pathlib import Path

import rodeo

_ANSIBLE = Path(rodeo.__file__).parent / "data" / "ansible"

_DOWNLOAD_TASKS = (
    _ANSIBLE / "roles" / "vms" / "tasks" / "images.yml",
    _ANSIBLE / "roles" / "pxe_server" / "tasks" / "files.yml",
    _ANSIBLE / "roles" / "pxe_server" / "tasks" / "tftp.yml",
)


def test_harvester_and_pxe_downloads_use_curl_ipv4_not_get_url():
    for path in _DOWNLOAD_TASKS:
        text = path.read_text()
        assert "ansible.builtin.get_url" not in text, f"{path.name} still uses get_url module"
        assert "curl -4" in text, f"{path.name} missing curl -4"
        assert "--http1.1" in text, f"{path.name} missing --http1.1"
        assert ".downloading" in text, f"{path.name} missing atomic temp download"


def test_harvester_iso_download_has_creates_guard_and_checksum_verify():
    text = (_ANSIBLE / "roles" / "vms" / "tasks" / "images.yml").read_text()
    assert "creates: \"{{ image_dir }}/harvester-v{{ harvester_version }}-amd64.iso\"" in text
    assert "Verify Harvester" in text
    assert "sha512sum" in text