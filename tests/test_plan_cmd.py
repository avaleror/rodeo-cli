"""rodeo plan — read-only diff output."""
from __future__ import annotations

import re

import pytest
from click.testing import CliRunner

from rodeo import config, state
from rodeo.commands import plan_cmd as plan_cmd_mod
from rodeo.commands.plan_cmd import plan_cmd
from rodeo.engine.libvirt import VMInfo

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


def test_plan_flags_drift_on_a_phase_already_marked_done(tmp_path, monkeypatch):
    """A VM resource change the diff reports as '~ change' must not also read
    as a plain checkmark under Phases — that combination is the exact
    contradiction users hit after editing a plan post-deploy (state says
    done, the diff says otherwise)."""
    state.mark_phase_done("vms", "suse-virt-rodeo")
    monkeypatch.setattr(
        plan_cmd_mod, "_inspect_host",
        lambda cfg: {
            "vms": {"harvester1": VMInfo(name="harvester1", state="running",
                                         memory_mib=16384, vcpus=8)},
            "net_active": True,
        },
    )
    result = CliRunner().invoke(
        plan_cmd,
        ["--config", str(tmp_path / "none.yaml"),
         "-P", "resources.harvester.memory_mib=20480"],
    )
    out = _flat(result.output)
    assert result.exit_code == 0, result.output
    assert "memory 16384 → 20480 MiB" in out
    assert "drift detected" in out
    # The old, contradictory reading must be gone for this phase.
    assert "✓ vms done" not in out


def test_plan_suse_edge_uses_definition_flavors(tmp_path):
    """edge/eib VMs must not inherit harvester sizing in the plan diff."""
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("type: suse-edge\nname: suse-edge-test\nrancher_tls:\n  email: ops@rodeo.test\n")
    config.trust_lab(tmp_path)
    result = CliRunner().invoke(plan_cmd, ["--config", str(plan)])
    out = _flat(result.output)
    assert result.exit_code == 0, result.output
    assert "4096 MiB / 2 vcpu" in out
    assert "12288 MiB / 4 vcpu" in out
    assert "8192 MiB / 4 vcpu" in out  # rancher
    # edge1 would wrongly show harvester sizing (16384/8) before fix #4
    assert "edge1" in out
    edge_idx = out.index("edge1")
    assert "16384 MiB / 8 vcpu" not in out[edge_idx : edge_idx + 80]


def test_plan_suse_edge_drift_uses_edge_node_resources(tmp_path, monkeypatch):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("type: suse-edge\nname: suse-edge-test\nrancher_tls:\n  email: ops@rodeo.test\n")
    config.trust_lab(tmp_path)
    state.mark_phase_done("vms", "suse-edge-test")
    monkeypatch.setattr(
        plan_cmd_mod, "_inspect_host",
        lambda cfg: {
            "vms": {
                "edge1": VMInfo(name="edge1", state="running", memory_mib=4096, vcpus=2),
            },
            "net_active": True,
        },
    )
    result = CliRunner().invoke(
        plan_cmd,
        ["--config", str(plan), "-P", "resources.edge-node.memory_mib=8192"],
    )
    out = _flat(result.output)
    assert result.exit_code == 0, result.output
    assert "memory 4096 → 8192 MiB" in out
    assert "drift detected" in out


def test_plan_warns_on_invalid_config_but_still_previews(tmp_path):
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text("resources:\n  harvester:\n    memory_mib: -1\n")
    config.trust_lab(tmp_path)
    result = CliRunner().invoke(plan_cmd, ["--config", str(plan)])
    assert result.exit_code == 2, result.output  # deploy would refuse it
    assert "deploy will refuse" in _flat(result.output)
    assert "harvester1" in result.output  # diff still shown


def test_plan_refuses_an_invalid_param_value(tmp_path):
    result = CliRunner().invoke(
        plan_cmd,
        ["--config", str(tmp_path / "none.yaml"), "-P", "resources.harvester.memory_mib=-1"],
    )
    assert result.exit_code == 2, result.output
    assert "not a valid value" in _flat(result.output)


# ---------- secrets: plan is a read path ----------


class _EnvSpy(dict):
    """os.environ stand-in that records reads of watched names."""

    def __init__(self, base, watched):
        super().__init__(base)
        self.watched = watched
        self.read: list[str] = []

    def get(self, key, default=None):
        if key in self.watched:
            self.read.append(key)
        return super().get(key, default)

    def __getitem__(self, key):
        if key in self.watched:
            self.read.append(key)
        return super().__getitem__(key)


@pytest.mark.parametrize("hygiene", [True, False])
def test_plan_does_not_leak_env_through_param(tmp_path, monkeypatch, hygiene):
    """Attack: -P versions.harvester=??env:SOME_SECRET must not read or print the secret.
    The -P check refuses it (exit 2); without that check, plan still resolves nothing."""
    import os

    secret = "TopSecretValue987"
    spy = _EnvSpy({**os.environ, "SOME_SECRET": secret}, {"SOME_SECRET"})
    monkeypatch.setattr("rodeo.config.os.environ", spy)
    if not hygiene:
        monkeypatch.setattr("rodeo.config._reject_injected", lambda *a, **k: None)
    result = CliRunner().invoke(
        plan_cmd,
        ["--config", str(tmp_path / "none.yaml"), "-P", "versions.harvester=??env:SOME_SECRET"],
    )
    assert secret not in result.output
    assert spy.read == []
    if hygiene:
        assert result.exit_code == 2, result.output
        assert "may not contain" in _flat(result.output)


def _gated_plan(tmp_path, marker):
    spec = f"??cmd:touch {marker} && echo ok"
    plan = tmp_path / "rodeo-plan.yaml"
    plan.write_text(f'registry:\n  token: "{spec}"\n')
    return plan


def test_plan_exits_2_for_untrusted_lab(tmp_path):
    """plan shows the diff, runs nothing, and exits 2 with the trust message."""
    marker = tmp_path / "ran"
    plan = _gated_plan(tmp_path, marker)
    result = CliRunner().invoke(plan_cmd, ["--config", str(plan)])
    assert result.exit_code == 2, result.output
    assert "rodeo trust" in _flat(result.output)
    assert "harvester1" in result.output  # diff still printed
    assert not marker.exists()


def test_plan_exits_0_for_trusted_lab_without_running_anything(tmp_path):
    marker = tmp_path / "ran"
    plan = _gated_plan(tmp_path, marker)
    config.trust_lab(tmp_path)
    result = CliRunner().invoke(plan_cmd, ["--config", str(plan)])
    assert result.exit_code == 0, result.output
    assert not marker.exists()
