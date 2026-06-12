"""rodeo plan — read-only diff output."""
from __future__ import annotations

import re

from click.testing import CliRunner

from rodeo import state
from rodeo.commands.plan_cmd import plan_cmd

# In the test environment libvirt-python is not installed, so the plan
# command degrades to desired-state-only mode — itself a code path worth
# covering (it's what users see on a laptop).

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _flat(output: str) -> str:
    """Collapse rich markup and line wrapping so substring asserts are reliable."""
    return " ".join(_ANSI.sub("", output).split())


def test_plan_shows_desired_state_without_libvirt(tmp_path):
    result = CliRunner().invoke(plan_cmd, ["--config", str(tmp_path / "none.yaml")])
    out = _flat(result.output)
    assert result.exit_code == 0, result.output
    assert "showing desired state only" in out
    assert "harvester1" in out
    assert "16384 MiB / 8 vcpu" in out
    assert "pending" in out
    assert "pxe_server" in out
    assert "ipxe.efi" in out
    assert "rodeo deploy" in out


def test_plan_reflects_param_override(tmp_path):
    result = CliRunner().invoke(
        plan_cmd,
        ["--config", str(tmp_path / "none.yaml"),
         "-P", "resources.harvester.memory_mib=20480"],
    )
    assert result.exit_code == 0, result.output
    assert "20480 MiB" in _flat(result.output)


def test_plan_shows_done_phases_and_instruqt_guard(tmp_path):
    state.mark_phase_done("kvm_host", "suse-virt-rodeo")
    result = CliRunner().invoke(
        plan_cmd,
        ["--config", str(tmp_path / "none.yaml"), "-P", "deployment_target=instruqt"],
    )
    assert result.exit_code == 0, result.output
    assert "done" in _flat(result.output)
    assert "guarded (instruqt)" in _flat(result.output)


def test_plan_warns_on_invalid_config_but_still_previews(tmp_path):
    result = CliRunner().invoke(
        plan_cmd,
        ["--config", str(tmp_path / "none.yaml"),
         "-P", "resources.harvester.memory_mib=-1"],
    )
    assert result.exit_code == 0, result.output
    assert "deploy will refuse" in _flat(result.output)
    assert "harvester1" in result.output  # diff still shown
