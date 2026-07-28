"""Large/binary downloads must use curl -4 via the shared common helper.

get_url uses Python urllib which prefers IPv6 when the remote has AAAA
records — broken on Instruqt (errno 101). All artifact fetches go through
roles/common/tasks/curl_fetch.yml so retries/IPv4 live in one place.
"""
from __future__ import annotations

from pathlib import Path

import yaml

import rodeo

_ANSIBLE = Path(rodeo.__file__).parent / "data" / "ansible"
_CURL_FETCH = _ANSIBLE / "roles" / "common" / "tasks" / "curl_fetch.yml"

_DOWNLOAD_CALLERS = (
    _ANSIBLE / "roles" / "vms" / "tasks" / "images.yml",
    _ANSIBLE / "roles" / "vms" / "tasks" / "rancher_image.yml",
    _ANSIBLE / "roles" / "vms" / "tasks" / "eib_image.yml",
    _ANSIBLE / "roles" / "pxe_server" / "tasks" / "files.yml",
    _ANSIBLE / "roles" / "pxe_server" / "tasks" / "tftp.yml",
)


def test_shared_curl_fetch_uses_ipv4_http11_and_atomic_temp():
    text = _CURL_FETCH.read_text()
    assert "ansible.builtin.get_url" not in text
    assert "curl -4" in text
    assert "--http1.1" in text
    assert ".downloading" in text
    assert "creates:" in text


def test_download_callers_include_shared_curl_fetch_not_inline_get_url():
    for path in _DOWNLOAD_CALLERS:
        text = path.read_text()
        assert "ansible.builtin.get_url" not in text, f"{path.name} uses get_url"
        assert "tasks_from: curl_fetch" in text, f"{path.name} missing curl_fetch include"


def test_harvester_iso_download_has_checksum_verify():
    text = (_ANSIBLE / "roles" / "vms" / "tasks" / "images.yml").read_text()
    assert "Verify Harvester" in text
    assert "sha512sum" in text


def test_ssh_key_owned_by_common_role():
    for rel in (
        "roles/vms/tasks/images.yml",
        "roles/pxe_server/tasks/main.yml",
    ):
        text = (_ANSIBLE / rel).read_text()
        assert "tasks_from: ensure_ssh_key" in text
        assert "ssh-keygen" not in text


def test_curl_fetch_task_file_is_valid_yaml():
    doc = yaml.safe_load(_CURL_FETCH.read_text())
    assert isinstance(doc, list)
    assert doc[0]["ansible.builtin.shell"]
