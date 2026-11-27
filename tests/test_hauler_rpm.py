"""The elemental RPMs come from a rolling OBS channel: a pinned file that is
gone upstream falls back to the newest build of the same package."""
from __future__ import annotations

import subprocess

from rodeo.engine.rancher.hauler import FETCH_RPM_FN, rpm_package

LISTING = ('<a href="./elemental-register-1.8.0~dev-slfo.1.2.9.x86_64.rpm">x</a>'
           '<a href="./elemental-register-1.8.0~dev-slfo.1.2.10.x86_64.rpm">x</a>'
           '<a href="./elemental-support-1.8.0~dev-slfo.1.2.9.x86_64.rpm">x</a>')


def test_package_name_from_url():
    assert rpm_package("https://h/r/elemental-register-1.8.0~dev-slfo.1.2.8.x86_64.rpm") == "elemental-register"
    assert rpm_package("https://h/r/elemental-system-agent-0.3.13-slfo.1.1.8.x86_64.rpm") == "elemental-system-agent"


def _run(tmp_path, pinned_ok: bool, listing: str):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "curl.log"
    # Fake curl: -o FILE URL downloads, a bare URL (the listing) prints it.
    pinned = 'echo ok > "$out"; exit 0' if pinned_ok else "exit 22"
    (bin_dir / "curl").write_text(f"""#!/bin/bash
out=""; url=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2;; --retry|--retry-delay) shift 2;; -*) shift;; *) url="$1"; shift;; esac; done
echo "$url" >> {log}
if [ -z "$out" ]; then printf '%s' {listing!r}; exit 0; fi
case "$url" in *slfo.1.2.8*) {pinned};; esac
echo ok > "$out"
""")
    (bin_dir / "curl").chmod(0o755)
    script = ("set -euo pipefail\n" + FETCH_RPM_FN +
              "fetch_rpm https://h/r/elemental-register-1.8.0~dev-slfo.1.2.8.x86_64.rpm "
              f"{tmp_path}/reg.rpm elemental-register\necho DONE\n")
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"PATH": f"{bin_dir}:/usr/bin:/bin"})
    return r, log.read_text().split()


def test_pinned_url_is_used_while_it_exists(tmp_path):
    r, urls = _run(tmp_path, True, LISTING)
    assert r.returncode == 0 and "DONE" in r.stdout
    assert urls == ["https://h/r/elemental-register-1.8.0~dev-slfo.1.2.8.x86_64.rpm"]


def test_gone_pin_falls_back_to_the_newest_build(tmp_path):
    r, urls = _run(tmp_path, False, LISTING)
    assert r.returncode == 0 and "DONE" in r.stdout
    assert urls[-1] == "https://h/r/elemental-register-1.8.0~dev-slfo.1.2.10.x86_64.rpm"


def test_no_build_at_all_fails_with_a_message(tmp_path):
    r, _ = _run(tmp_path, False, "<html></html>")
    assert r.returncode != 0 and "DONE" not in r.stdout
    assert "no elemental-register rpm" in r.stderr
