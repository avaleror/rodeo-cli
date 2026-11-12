"""Lab-host install speed-ups: zypper from the EC2 boot script, galaxy in parallel."""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from rodeo.commands import install_deps as mod
from rodeo.providers.aws import AwsHostProvider

_BASE = {
    "type": "aws",
    "region": "eu-north-1",
    "subnet_id": "subnet-1",
    "instance_type": "m8id.4xlarge",
    "ami": "ami-1",
    "security_group_ids": ["sg-1"],
}


@pytest.fixture
def ssh_home(tmp_path, monkeypatch):
    ssh_dir = tmp_path / "ssh"
    monkeypatch.setattr("rodeo.paths.rodeo_ssh_dir", lambda: ssh_dir)
    monkeypatch.setattr("rodeo.ssh_key.rodeo_ssh_dir", lambda: ssh_dir)
    return ssh_dir


def _userdata(**kw):
    kwargs = AwsHostProvider(ec2_client=object())._run_instances_kwargs(
        dict(_BASE), host_id="primary", workshop="lab", **kw
    )
    return yaml.safe_load(kwargs["UserData"])


# ---------- boot script ----------

def test_lab_host_installs_the_install_deps_set_at_boot(ssh_home):
    cfg = _userdata(lab_host=True)
    files = {f["path"]: f["content"] for f in cfg["write_files"]}
    script = files["/usr/local/sbin/rodeo-boot.sh"]
    # Same patterns and packages as install-deps, so it finds them done.
    for name in mod._ZYPPER_PATTERNS:
        assert f"pattern:{name}" in script
    for name in mod._ZYPPER_PACKAGES + ["ansible-core"]:
        assert f" {name}" in script
    # Same recommends policy as install-deps (no --no-recommends there).
    assert "--no-recommends" not in script
    assert "ZYPP_LOCK_TIMEOUT=600" in files["/etc/profile.d/zz-rodeo-zypp.sh"]
    # Started in the background: cloud-init and SSH never wait for it.
    assert [
        "systemd-run", "--unit=rodeo-boot", "--collect", "/bin/bash", "/usr/local/sbin/rodeo-boot.sh",
    ] in cfg["runcmd"]
    # Access setup and the dead-man switch are untouched.
    assert "/etc/sudoers.d/90-rodeo" in files
    assert ["systemctl", "enable", "--now", "rodeo-deadman.timer"] in cfg["runcmd"]


def test_portal_and_default_hosts_get_no_boot_install(ssh_home):
    cfg = _userdata()
    paths = {f["path"] for f in cfg["write_files"]}
    assert "/usr/local/sbin/rodeo-boot.sh" not in paths
    assert not any("rodeo-boot" in str(c) for c in cfg["runcmd"])


def test_provisioned_lab_hosts_ask_for_the_boot_install(ssh_home, monkeypatch):
    seen = {}

    def fake_kwargs(self, config, **kw):
        seen.update(kw)
        raise RuntimeError("stop")

    monkeypatch.setattr(AwsHostProvider, "_run_instances_kwargs", fake_kwargs)
    spec = SimpleNamespace(workshop="lab", ssh_user="ec2-user", extra_labels={})
    with pytest.raises(RuntimeError):
        AwsHostProvider(ec2_client=object())._create(object(), spec, dict(_BASE), "primary")
    assert seen["lab_host"] is True


def test_boot_script_is_valid_bash():
    r = subprocess.run(["bash", "-n"], input=mod.zypper_boot_install_script(), text=True)
    assert r.returncode == 0


# ---------- galaxy in the background ----------

def _fake_galaxy(monkeypatch, *, code: int, output: str):
    real_popen = subprocess.Popen

    def popen(cmd, **kw):
        assert cmd[:3] == ["ansible-galaxy", "collection", "install"]
        return real_popen(
            [sys.executable, "-c", f"import sys; print({output!r}); sys.exit({code})"], **kw
        )

    monkeypatch.setattr(mod.subprocess, "Popen", popen)


def test_galaxy_success_writes_the_runner_marker(monkeypatch, tmp_path):
    _fake_galaxy(monkeypatch, code=0, output="installed")
    monkeypatch.setattr(mod, "invoking_home", lambda: tmp_path)
    monkeypatch.setattr(mod.Path, "home", lambda: tmp_path)
    marked = []
    monkeypatch.setattr(mod, "mark_collections_installed", lambda req: marked.append(req))
    mod._finish_ansible_collections(mod._start_ansible_collections())
    assert marked == [mod._REQUIREMENTS]


def test_galaxy_under_plain_sudo_leaves_no_marker(monkeypatch, tmp_path):
    # Collections went to root's home, the marker would live in the user's:
    # a marker there would make the runner skip a needed install.
    _fake_galaxy(monkeypatch, code=0, output="installed")
    monkeypatch.setattr(mod, "invoking_home", lambda: tmp_path / "user")
    monkeypatch.setattr(mod.Path, "home", lambda: tmp_path / "root")
    marked = []
    monkeypatch.setattr(mod, "mark_collections_installed", lambda req: marked.append(req))
    mod._finish_ansible_collections(mod._start_ansible_collections())
    assert marked == []


def test_galaxy_failure_is_reported_and_not_marked(monkeypatch, tmp_path, capsys):
    _fake_galaxy(monkeypatch, code=1, output="ERROR! galaxy.ansible.com unreachable")
    monkeypatch.setattr(mod, "invoking_home", lambda: tmp_path)
    monkeypatch.setattr(mod.Path, "home", lambda: tmp_path)
    marked = []
    monkeypatch.setattr(mod, "mark_collections_installed", lambda req: marked.append(req))
    mod._finish_ansible_collections(mod._start_ansible_collections())
    out = capsys.readouterr().out
    assert "unreachable" in out and "ansible-galaxy collection install failed" in out
    assert marked == []


def test_suse_starts_galaxy_before_the_kvm_packages(monkeypatch):
    order = []
    monkeypatch.setattr(mod.os, "geteuid", lambda: 0)
    monkeypatch.setattr(mod, "_detect_distro", lambda: "suse")
    monkeypatch.setattr(mod, "_install_ansible_core", lambda d: order.append("ansible-core"))
    monkeypatch.setattr(mod, "_start_ansible_collections", lambda: order.append("galaxy-start") or None)
    monkeypatch.setattr(mod, "_install_suse", lambda: order.append("zypper"))
    monkeypatch.setattr(mod, "_finish_ansible_collections", lambda g: order.append("galaxy-wait"))
    monkeypatch.setattr(mod, "_ensure_invoking_user_in_libvirt_group", lambda: None)
    from click.testing import CliRunner

    r = CliRunner().invoke(mod.install_deps_cmd, [])
    assert r.exit_code == 0, r.output
    assert order == ["ansible-core", "galaxy-start", "zypper", "galaxy-wait"]
