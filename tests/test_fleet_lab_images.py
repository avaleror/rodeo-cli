"""rodeo fleet lab-images and the virt-rodeo 55-sles-images.sh script."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

import rodeo
from rodeo.config import ConfigError
from rodeo.fleet import lab_images as li
from rodeo.fleet.inventory import load_inventory
from rodeo.fleet.ssh_exec import RemoteResult

SCRIPT = Path(rodeo.__file__).parent / "data" / "examples" / "virt-rodeo" / "custom" / "scripts" / "55-sles-images.sh"


def _sign(bucket, key, seconds):
    return f"https://{bucket}.s3.example/{key}?X-Amz-Expires={seconds}&X-Amz-Signature=sig"


def _inv(tmp_path, hosts=2):
    data = {"name": "ws", "lab": {"dir": "/root/lab", "profile": "virt-rodeo"},
            "provider": {"type": "aws", "region": "eu-north-1", "subnet_id": "subnet-1",
                         "instance_type": "m8id.8xlarge"},
            "hosts": [{"id": f"lab-{i}", "ssh": f"10.0.0.{i}"} for i in range(1, hosts + 1)]}
    p = tmp_path / "workshop.yaml"
    p.write_text(yaml.safe_dump(data))
    return load_inventory(p)


def test_list_has_both_images_and_flags_sles16_for_harvester():
    text = li.list_text("rodeo-lab-images-x", "images/", 3600, _sign)
    lines = text.strip().splitlines()
    assert lines[0].startswith("SLES15-SP7-Minimal-VM.x86_64-Cloud-GM.qcow2 https://") and not lines[0].endswith("harvester")
    assert lines[1].startswith("SLES-16.0-Minimal-VM.x86_64-Cloud-GM.qcow2 https://") and lines[1].endswith(" harvester")
    assert "images/SLES-16.0" in lines[1]


@pytest.mark.parametrize("kw", [{"bucket": "Bad_Bucket"}, {"prefix": "../x/"}, {"seconds": 10}])
def test_list_rejects_bad_input(kw):
    args = {"bucket": "rodeo-lab-images-x", "prefix": "images/", "seconds": 3600, **kw}
    with pytest.raises(ConfigError):
        li.list_text(args["bucket"], args["prefix"], args["seconds"], _sign)


def test_links_travel_on_stdin_never_argv(tmp_path, monkeypatch):
    calls = []

    def fake(inventory, host, argv, *, timeout=120.0, as_root=True, stdin=None):
        calls.append((argv, stdin))
        return RemoteResult(host.id, 0, ">>> [sles-images] custom_scripts step complete.\n", "")

    monkeypatch.setattr(li, "run_remote", fake)
    rows = li.fleet_lab_images(_inv(tmp_path), bucket="rodeo-lab-images-x", sign=_sign, run=True)
    assert all(r.ok for r in rows) and len(rows) == 2
    writes = [c for c in calls if c[1]]
    assert len(writes) == 2 and all("X-Amz-Signature" in s for _, s in writes)
    assert all("X-Amz-Signature" not in " ".join(a) for a, _ in calls)
    assert ["bash", "/root/lab/custom/scripts/55-sles-images.sh"] in [a for a, _ in calls]


# -- the host script, with fake curl and kubectl ----------------------------------

def _run_script(tmp_path, list_body: str | None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # curl writes a qcow2 header (or garbage for ".../bad") to the -o file.
    (bin_dir / "curl").write_text('#!/bin/bash\nout=""; url=""\nwhile [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift;; https://*) url="$1";; esac; shift; done\n'
                                  'case "$url" in *bad*) printf "nope" > "$out";; *) printf "QFI\\xfbrest" > "$out";; esac\n')
    (bin_dir / "kubectl").write_text('#!/bin/bash\necho "$*" >> "$KLOG"\n'
                                     'case "$*" in *"get virtualmachineimages"*"displayName"*) [ -f "$KSTATE" ] && cat "$KSTATE";;'
                                     ' *"create -f -"*) cat >/dev/null; printf "image-1 SLES-16.0-Minimal-VM.x86_64-Cloud-GM.qcow2\\nSLES-16.0-Minimal-VM.x86_64-Cloud-GM.qcow2 100\\n" > "$KSTATE";;'
                                     ' *"create namespace"*) echo "apiVersion: v1";; *) cat >/dev/null 2>&1;; esac\n')
    (bin_dir / "nft").write_text('#!/bin/bash\n[ "$1" = "-c" ] && shift; [ "$1" = "-f" ] && cp "$2" "$NFT_LAST"; exit 0\n')
    (bin_dir / "systemctl").write_text("#!/bin/bash\nexit 0\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    lst = tmp_path / "lab-images.list"
    if list_body is not None:
        lst.write_text(list_body)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "RODEO_LAB_IMAGES_LIST": str(lst),
           "RODEO_IMAGE_CACHE_DIR": str(tmp_path / "cache"), "KLOG": str(tmp_path / "klog"),
           "KSTATE": str(tmp_path / "kstate"), "NFT_LAST": str(tmp_path / "nft-last"),
           "RODEO_IMAGE_CACHE_GUARD": str(tmp_path / "guard.nft"), "RODEO_SYSTEMD_DIR": str(tmp_path)}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)


def test_script_without_a_list_skips_cleanly(tmp_path):
    res = _run_script(tmp_path, None)
    assert res.returncode == 0 and "skipping" in res.stdout


def test_script_caches_registers_and_never_prints_links(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    body = li.list_text("rodeo-lab-images-x", "images/", 3600, _sign)
    res = _run_script(tmp_path, body)
    assert res.returncode == 0, res.stdout + res.stderr
    cache = tmp_path / "cache"
    assert (cache / "SLES15-SP7-Minimal-VM.x86_64-Cloud-GM.qcow2").read_bytes().startswith(b"QFI\xfb")
    assert "is Active in official-images" in res.stdout
    assert "X-Amz-Signature" not in res.stdout + res.stderr
    # Licensed copies are root-only, the links are gone, the cache serves the nodes only.
    assert oct((cache / "SLES15-SP7-Minimal-VM.x86_64-Cloud-GM.qcow2").stat().st_mode & 0o777) == "0o600"
    assert not (tmp_path / "lab-images.list").exists()
    rule = (tmp_path / "nft-last").read_text()
    assert "ip daddr 192.168.122.1 tcp dport 8889 ip saddr != { 192.168.122.11-192.168.122.13 } drop" in rule
    (tmp_path / "lab-images.list").write_text(body)
    # Second run: nothing downloaded again, nothing created again.
    res2 = _run_script_again(tmp_path)
    assert "already cached" in res2.stdout and "already registered" in res2.stdout


def _run_script_again(tmp_path):
    env = {"PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}", "RODEO_LAB_IMAGES_LIST": str(tmp_path / "lab-images.list"),
           "RODEO_IMAGE_CACHE_DIR": str(tmp_path / "cache"), "KLOG": str(tmp_path / "klog"),
           "KSTATE": str(tmp_path / "kstate"), "NFT_LAST": str(tmp_path / "nft-last"),
           "RODEO_IMAGE_CACHE_GUARD": str(tmp_path / "guard.nft"), "RODEO_SYSTEMD_DIR": str(tmp_path)}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)


def test_script_rejects_non_qcow2_downloads(tmp_path):
    res = _run_script(tmp_path, "SLES15-SP7-Minimal-VM.x86_64-Cloud-GM.qcow2 https://x.example/bad\n")
    assert res.returncode == 1 and "FAILED to download" in res.stdout
    assert (tmp_path / "lab-images.list").exists(), "keep the list for a retry when something failed"
    assert not (tmp_path / "cache" / "SLES15-SP7-Minimal-VM.x86_64-Cloud-GM.qcow2").exists()


def test_only_virt_rodeo_has_the_script():
    examples = Path(rodeo.__file__).parent / "data" / "examples"
    assert SCRIPT.stat().st_mode & 0o111
    assert not (examples / "virt-workshop" / "custom" / "scripts" / "55-sles-images.sh").exists()
