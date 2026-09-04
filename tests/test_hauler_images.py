"""Hauler must mirror images that actually exist at registry.suse.com.

Regression: rancher/elemental-register was never a real repository there
(confirmed live: registry returns NAME_UNKNOWN) — the register agent ships
inside the elemental-operator image itself, same tag as the operator's own
Deployment (confirmed live: that exact image+tag pulls fine).
"""
from __future__ import annotations

from pathlib import Path

import rodeo.engine.rancher.hauler as hauler_mod
from rodeo.engine.rancher import RancherPhase


def test_hauler_does_not_reference_nonexistent_elemental_register_image():
    source = Path(hauler_mod.__file__).read_text()
    assert "rancher/elemental-register:" not in source
    assert "rancher/elemental-operator:{self.elemental_op_version}" in source


def test_hauler_fileserver_curl_waits_for_the_service_to_be_listening():
    """Regression: enable --now returns once systemd forks the unit, not once the
    fileserver is actually bound — curling immediately raced startup and failed
    "Could not connect to server". Must poll before staging the base images."""
    source = Path(hauler_mod.__file__).read_text()
    enable_idx = source.index("systemctl enable --now hauler-registry.service")
    first_stage_curl_idx = source.index('curl -fsSL "http://localhost:8080/{iso_fname}"')
    between = source[enable_idx:first_stage_curl_idx]
    assert "seq 1 30" in between
    assert "sleep 1" in between


def test_hauler_store_add_file_names_are_lowercase_and_downloaded_via_curl():
    """Regression: hauler's `store add file` reference-name parser rejects
    uppercase (confirmed live: "could not parse reference" with no --name given,
    since openSUSE's real filenames are "openSUSE-Leap-Micro..."). Files must be
    curl'd to a local path first (hauler's own Go HTTP client also failed TLS
    negotiation against at least one opensuse.org mirror) and added with an
    explicit lowercase --name, not passed as a remote URL directly."""
    cfg = {
        "network": {"vip": "10.0.0.10", "rancher_ip": "10.0.0.9",
                    "gateway": "10.0.0.1", "dns_domain": "lab.example"},
        "credentials": {"harvester_admin_password": "x", "rancher_admin_password": "x"},
        "vms": {"rancher": {}},
    }
    phase = RancherPhase(cfg)
    captured = {}

    def fake_run(cmd, **kw):
        captured["script"] = kw.get("input", "")
        import subprocess
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    phase._run = fake_run
    gen = phase._populate_hauler()
    try:
        while True:
            next(gen)
    except StopIteration:
        pass

    script = captured["script"]
    add_file_lines = [line for line in script.splitlines() if "store add file" in line]
    assert add_file_lines, "expected at least one 'store add file' line"
    for line in add_file_lines:
        assert "https://" not in line, f"store add file must use a local path, not a URL: {line}"
        assert "--name" in line
        name = line.split('--name "')[1].split('"')[0]
        assert name == name.lower(), f"hauler rejects uppercase reference names: {name!r}"
