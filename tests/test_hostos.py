"""Which machines can host labs, and what `up`/`deploy` do on the others."""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from rodeo import hostos
from rodeo.commands import up_cmd as up_mod

SLES16 = {"ID": "sles", "VERSION_ID": "16.0", "PRETTY_NAME": "SUSE Linux Enterprise Server 16.0"}
LEAP16 = {"ID": "opensuse-leap", "VERSION_ID": "16.0", "PRETTY_NAME": "openSUSE Leap 16.0"}
SLES15 = {"ID": "sles", "VERSION_ID": "15.6", "PRETTY_NAME": "SUSE Linux Enterprise Server 15 SP6"}
UBUNTU = {"ID": "ubuntu", "VERSION_ID": "24.04", "PRETTY_NAME": "Ubuntu 24.04.3 LTS"}


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv(hostos.ALLOW_ENV, raising=False)


@pytest.mark.parametrize("rel", [SLES16, LEAP16])
def test_sles_and_leap_16_host_labs(rel):
    assert hostos.kvm_host_problem(system="Linux", os_release=rel) == ""


@pytest.mark.parametrize("rel, name", [(SLES15, "SUSE Linux Enterprise Server 15 SP6"),
                                       (UBUNTU, "Ubuntu 24.04.3 LTS")])
def test_other_linux_is_a_control_plane(rel, name):
    problem = hostos.kvm_host_problem(system="Linux", os_release=rel)
    assert problem.startswith(name) and "SLES 16 / Leap 16" in problem


def test_macos_is_a_control_plane():
    assert hostos.kvm_host_problem(system="Darwin").startswith("macOS cannot host labs")


def test_override_for_unsupported_kvm_hosts(monkeypatch):
    monkeypatch.setenv(hostos.ALLOW_ENV, "1")
    assert hostos.kvm_host_problem(system="Linux", os_release=UBUNTU) == ""


def test_os_release_parsing(tmp_path):
    f = tmp_path / "os-release"
    f.write_text('NAME="SLES"\nID="sles"\nVERSION_ID="16.0"\n# comment\n')
    assert hostos._os_release(f)["VERSION_ID"] == "16.0"
    assert hostos._os_release(tmp_path / "missing") == {}


def _on_macos(monkeypatch):
    monkeypatch.setattr(
        "rodeo.hostos.kvm_host_problem",
        lambda **kw: "macOS cannot host labs (they need Linux with KVM and libvirt)",
    )
    # Nothing below may touch the host: these would install packages.
    monkeypatch.setattr(up_mod, "_ensure_host_ready", lambda *a, **k: pytest.fail("host deps"))
    monkeypatch.setattr(up_mod, "_deploy", lambda *a, **k: pytest.fail("local deploy"))


def test_up_refuses_a_local_lab_on_a_control_plane(tmp_path, monkeypatch):
    _on_macos(monkeypatch)
    result = CliRunner().invoke(
        up_mod.up_cmd, ["--yes", "--no-tmux", "--profile", "rancher", "--dir", str(tmp_path / "lab"),
                        "--target", "baremetal"],
    )
    assert result.exit_code == 2
    assert "macOS cannot host labs" in result.output
    assert "--target aws" in result.output


def test_up_no_deploy_still_seeds_on_a_control_plane(tmp_path, monkeypatch):
    _on_macos(monkeypatch)
    lab = tmp_path / "lab"
    result = CliRunner().invoke(
        up_mod.up_cmd, ["--yes", "--no-tmux", "--no-deploy", "--profile", "rancher", "--dir", str(lab),
                        "--target", "baremetal"],
    )
    assert result.exit_code == 0, result.output
    assert (lab / "rodeo-plan.yaml").is_file()


def test_lab_in_a_box_profiles_are_not_local_kvm_deploys(tmp_path):
    assert up_mod._runs_on_this_machine(None, "smlm-workshop") is False
    assert up_mod._runs_on_this_machine(None, "rancher") is True


def test_deploy_refuses_on_a_control_plane(tmp_path, monkeypatch):
    from rodeo.commands import deploy as deploy_mod
    from rodeo.labseed import seed_lab

    from rodeo.secretgen import ensure_secrets_file

    ensure_secrets_file()
    lab = seed_lab("rancher", tmp_path / "lab")
    monkeypatch.setattr(deploy_mod, "is_root", lambda: True)
    monkeypatch.setattr(
        "rodeo.hostos.kvm_host_problem",
        lambda **kw: "Ubuntu 24.04.3 LTS is not a supported KVM host (labs run on SLES 16 / Leap 16)",
    )
    monkeypatch.setattr(deploy_mod, "execute_deploy", lambda *a, **k: pytest.fail("deployed"))
    result = CliRunner().invoke(deploy_mod.deploy_cmd, ["--config-dir", str(lab)])
    assert result.exit_code == 2
    assert "not a supported KVM host" in result.output
