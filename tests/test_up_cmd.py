"""rodeo up — the on-ramp flow (engine + host mocked)."""
from __future__ import annotations

import yaml
from click.testing import CliRunner

from rodeo.commands import up_cmd as up_mod


def _ready_host():
    return {
        "is_root": True, "pkg_mgr": "zypper", "has_kvm": True, "nested": True,
        "ram_total_gib": 64, "ram_avail_gib": 64, "cpus": 32,
        "image_dir": "/var/lib/libvirt/images", "disk_free_gib": 900,
        "core_tools": {"ansible-playbook": True, "ansible-galaxy": True, "kubectl": True},
        "optional_tools": {"virsh": True, "ssh": True},
    }


def test_up_no_deploy_seeds_and_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(up_mod, "detect_host", _ready_host)
    lab = tmp_path / "labs" / "demo"

    result = CliRunner().invoke(
        up_mod.up_cmd,
        ["--no-deploy", "--yes", "--profile", "test", "--dir", str(lab)],
    )
    assert result.exit_code == 0, result.output

    plan = lab / "rodeo-plan.yaml"
    assert plan.exists()
    data = yaml.safe_load(plan.read_text())
    assert data["deployment_target"] == "baremetal"
    assert data["credentials"]["harvester_os_password"].startswith("??")
    assert not data["credentials"]["harvester_os_password"].startswith("??env:")

    # Secrets generated silently in the (isolated) home — no env/source needed.
    assert (tmp_path / ".rodeo" / "secrets.yaml").exists()
    assert "Ready" in result.output


def test_up_uses_existing_lab(tmp_path, monkeypatch):
    monkeypatch.setattr(up_mod, "detect_host", _ready_host)
    from rodeo.labseed import seed_lab
    lab = seed_lab("test", tmp_path / "existing")

    result = CliRunner().invoke(
        up_mod.up_cmd, ["--no-deploy", "--yes", "--dir", str(lab)]
    )
    assert result.exit_code == 0, result.output
    assert "Using existing lab" in result.output


def test_up_deploys_when_root(tmp_path, monkeypatch):
    monkeypatch.setattr(up_mod, "detect_host", _ready_host)
    monkeypatch.setattr(up_mod, "is_root", lambda: True)
    monkeypatch.setattr(up_mod, "run_preflight", lambda cfg, root: True)

    captured = {}

    def fake_exec(cfg, root):
        captured["name"] = cfg["name"]
        return 0

    monkeypatch.setattr(up_mod, "execute_deploy", fake_exec)
    lab = tmp_path / "labs" / "deployme"

    result = CliRunner().invoke(
        up_mod.up_cmd, ["--yes", "--profile", "test", "--dir", str(lab)]
    )
    assert result.exit_code == 0, result.output
    assert captured.get("name") == "deployme"


def test_up_offers_install_when_deps_missing(tmp_path, monkeypatch):
    host = _ready_host()
    host["has_kvm"] = False
    host["core_tools"]["kubectl"] = False
    monkeypatch.setattr(up_mod, "detect_host", lambda *a, **k: host)

    # Decline the install prompt -> command stops with guidance.
    result = CliRunner().invoke(
        up_mod.up_cmd, ["--no-deploy", "--dir", str(tmp_path / "l")],
        input="n\n",
    )
    assert result.exit_code == 1
    assert "install-deps" in result.output
