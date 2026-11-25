"""virt-rodeo's NFS backup target: its own nosuid,nodev,noexec filesystem.

The script needs root and a real loop device, so these tests run a copy of
it with every path moved under tmp_path and fake mount/findmnt/exportfs/
mkfs/systemctl binaries first on PATH. The fakes keep a mount table in a
text file and log every call, which is enough to check the decisions the
script makes: what it mounts, with which options, when it refuses, and
that `exportfs -ra` never runs on an unhardened mount.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import yaml

from rodeo.labseed import PROFILE_EXAMPLE, example_dir, seed_lab
from rodeo.providers.instance_catalog import catalog_for_profile

SCRIPT = example_dir("virt-rodeo") / "custom" / "scripts" / "60-nfs-backup-target.sh"
WORKSHOP_SCRIPT = example_dir("virt-workshop") / "custom" / "scripts" / "60-nfs-backup-target.sh"
HARDENED = ("nosuid", "nodev", "noexec")

# Mount table: one "target|source|options" line per mount.
FAKES = {
    "findmnt": r"""#!/bin/bash
# findmnt -no OPTIONS|SOURCE --mountpoint DIR
field="$2"; dir="$4"
line="$(grep "^${dir}|" "$FAKE_STATE/mounts" 2>/dev/null | tail -1)"
[ -n "$line" ] || exit 1
IFS='|' read -r t s o <<<"$line"
[ "$field" = OPTIONS ] && echo "$o" || echo "$s"
""",
    "mountpoint": r"""#!/bin/bash
grep -q "^${2}|" "$FAKE_STATE/mounts" 2>/dev/null
""",
    "mount": r"""#!/bin/bash
echo "mount $*" >> "$FAKE_STATE/log"
# mount -o OPTS [SRC] TGT
opts="$2"; shift 2
if [ "$#" -eq 1 ]; then
  tgt="$1"
  line="$(grep "^${tgt}|" "$FAKE_STATE/mounts" | tail -1)"
  IFS='|' read -r t s o <<<"$line"
  grep -v "^${tgt}|" "$FAKE_STATE/mounts" > "$FAKE_STATE/m.new"; mv "$FAKE_STATE/m.new" "$FAKE_STATE/mounts"
  echo "${tgt}|${s}|${o},${opts#remount,}" >> "$FAKE_STATE/mounts"
else
  echo "${2}|${1}|rw,${opts}" >> "$FAKE_STATE/mounts"
fi
""",
    "umount": r"""#!/bin/bash
echo "umount $*" >> "$FAKE_STATE/log"
# Data written to a mounted image lands "in the image" when it is unmounted.
mkdir -p "$FAKE_STATE/img"
cp -a "$1/." "$FAKE_STATE/img/" && find "$1" -mindepth 1 -delete
grep -v "^${1}|" "$FAKE_STATE/mounts" > "$FAKE_STATE/m.new"; mv "$FAKE_STATE/m.new" "$FAKE_STATE/mounts"
""",
    "losetup": "#!/bin/bash\nexit 1\n",
    "mkfs.ext4": '#!/bin/bash\necho "mkfs.ext4 $*" >> "$FAKE_STATE/log"\n',
    "exportfs": r"""#!/bin/bash
echo "exportfs $*" >> "$FAKE_STATE/log"
[ "$1" = "-v" ] && echo "$FAKE_BACKUP_DIR 192.168.122.0/24(rw)"
exit 0
""",
    "systemctl": "#!/bin/bash\nexit 0\n",
}


def _harness(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "root"
    for d in ("srv", "etc", "run", "var/lib", "state", "bin"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "etc" / "fstab").write_text("UUID=abc / ext4 defaults 0 1\n")
    for name, body in FAKES.items():
        f = root / "bin" / name
        f.write_text(body)
        f.chmod(0o755)
    text = SCRIPT.read_text()
    for real in ("/srv/backups", "/etc/fstab", "/etc/exports", "/var/lib/rodeo-nfs", "/run/rodeo-nfs-migrate"):
        text = text.replace(real, f"{root}{real}")
    script = root / "nfs.sh"
    script.write_text(text)
    env = {
        "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
        "FAKE_STATE": str(root / "state"),
        "FAKE_BACKUP_DIR": f"{root}/srv/backups",
        "RODEO_NFS_BACKUP_SIZE": "1M",
    }
    return root, env


def _run(root: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(root / "nfs.sh")], env=env, capture_output=True, text=True, timeout=30)


def _log(root: Path) -> str:
    p = root / "state" / "log"
    return p.read_text() if p.exists() else ""


def _mounts(root: Path) -> list[str]:
    p = root / "state" / "mounts"
    return p.read_text().splitlines() if p.exists() else []


def test_script_text_checks_options_before_export():
    text = SCRIPT.read_text()
    assert 'MOUNT_OPTS="loop,nosuid,nodev,noexec"' in text
    # The last hardening check sits right before the export.
    guard = text.rindex("mount_is_hardened || fail")
    assert guard < text.index("exportfs -ra")
    # Longhorn writes as root: no_root_squash stays.
    assert "no_root_squash" in text
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_virt_workshop_script_is_untouched():
    """Additive rule: the hardening lives in virt-rodeo only."""
    assert "MOUNT_OPTS" not in WORKSHOP_SCRIPT.read_text()


def test_fresh_host_mounts_image_hardened_then_exports(tmp_path):
    root, env = _harness(tmp_path)
    res = _run(root, env)
    assert res.returncode == 0, res.stdout + res.stderr

    img = f"{root}/var/lib/rodeo-nfs/backups.ext4"
    assert Path(img).is_file()
    assert oct(Path(img).stat().st_mode & 0o777) == "0o600"
    log = _log(root)
    assert f"mkfs.ext4 -q -F -L rodeo-backups {img}" in log
    assert f"mount -o loop,nosuid,nodev,noexec {img} {root}/srv/backups" in log
    assert log.index("mount -o") < log.index("exportfs -ra")

    fstab = (root / "etc" / "fstab").read_text().splitlines()
    assert f"{img} {root}/srv/backups ext4 loop,nosuid,nodev,noexec 0 0" in fstab
    assert fstab[0].startswith("UUID=abc /")


def test_rerun_changes_nothing(tmp_path):
    root, env = _harness(tmp_path)
    assert _run(root, env).returncode == 0
    (root / "state" / "log").write_text("")
    fstab_before = (root / "etc" / "fstab").read_text()

    res = _run(root, env)
    assert res.returncode == 0, res.stdout + res.stderr
    log = _log(root)
    assert "mkfs" not in log
    assert "mount -o" not in log
    assert "exportfs -u" not in log  # no needless unexport on a healthy re-run
    assert (root / "etc" / "fstab").read_text() == fstab_before


def test_existing_plain_directory_is_migrated(tmp_path):
    root, env = _harness(tmp_path)
    old = root / "srv" / "backups" / "backupstore" / "vol.cfg"
    old.parent.mkdir(parents=True)
    old.write_text("longhorn")

    res = _run(root, env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (root / "state" / "img" / "backupstore" / "vol.cfg").read_text() == "longhorn"
    assert not old.exists()
    assert any(m.startswith(f"{root}/srv/backups|") for m in _mounts(root))


def test_own_mount_that_lost_options_is_remounted(tmp_path):
    root, env = _harness(tmp_path)
    assert _run(root, env).returncode == 0
    img = f"{root}/var/lib/rodeo-nfs/backups.ext4"
    (root / "state" / "mounts").write_text(f"{root}/srv/backups|{img}|rw,loop\n")
    (root / "state" / "log").write_text("")

    res = _run(root, env)
    assert res.returncode == 0, res.stdout + res.stderr
    log = _log(root)
    assert f"mount -o remount,nosuid,nodev,noexec {root}/srv/backups" in log
    assert log.index("exportfs -u") < log.index("mount -o remount")
    assert "exportfs -ra" in log


def test_foreign_unhardened_mount_is_refused(tmp_path):
    root, env = _harness(tmp_path)
    (root / "state" / "mounts").write_text(f"{root}/srv/backups|/dev/sdb1|rw,relatime\n")

    res = _run(root, env)
    assert res.returncode == 1
    assert "did not create" in res.stderr
    log = _log(root)
    assert "mount -o" not in log
    assert "exportfs -ra" not in log
    assert "rodeo-nfs" not in (root / "etc" / "fstab").read_text()


def test_foreign_hardened_mount_is_left_alone(tmp_path):
    root, env = _harness(tmp_path)
    (root / "state" / "mounts").write_text(f"{root}/srv/backups|/dev/sdb1|rw,nosuid,nodev,noexec\n")

    res = _run(root, env)
    assert res.returncode == 0, res.stdout + res.stderr
    log = _log(root)
    assert "mount -o" not in log
    assert "exportfs -ra" in log
    assert "rodeo-nfs" not in (root / "etc" / "fstab").read_text()


def test_virt_rodeo_profile_seeds_without_checks(tmp_path):
    assert PROFILE_EXAMPLE["virt-rodeo"] == "virt-rodeo"
    lab = seed_lab("virt-rodeo", tmp_path / "virt-rodeo", deployment_target="aws")
    assert not (lab / "checks").exists()

    plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text())
    assert plan["deployment_target"] == "aws"
    assert plan["resources"]["harvester"]["disk_gb"] == 500

    scripts_dir = lab / "custom" / "scripts"
    scripts = sorted(f.name for f in scripts_dir.iterdir() if f.is_file())
    assert scripts == ["50-image-cache.sh", "60-nfs-backup-target.sh", "65-student-kit.sh", "70-webserver-prod.sh"]
    for name in scripts:
        assert (scripts_dir / name).stat().st_mode & stat.S_IXUSR, f"{name} must be executable"


def test_virt_rodeo_catalog_matches_virt_workshop():
    for provider in ("aws", "gcp"):
        rodeo = catalog_for_profile("virt-rodeo", provider)
        workshop = catalog_for_profile("virt-workshop", provider)
        assert {t: o.instance_type for t, o in rodeo.items()} == {t: o.instance_type for t, o in workshop.items()}
