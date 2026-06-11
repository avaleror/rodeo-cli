"""rodeo logs --bundle support bundle."""
from __future__ import annotations

import tarfile

from click.testing import CliRunner

from rodeo import state
from rodeo.commands.logs import logs_cmd


def test_bundle_collects_and_redacts(tmp_path):
    log_dir = tmp_path / "qemu"
    log_dir.mkdir()
    (log_dir / "harvester1_serial.log").write_text("boot line 1\nboot line 2\n")

    # default plan name when no rodeo-plan.yaml exists
    state.mark_phase_done("kvm_host", "suse-virt-rodeo")

    out = tmp_path / "bundle.tar.gz"
    result = CliRunner().invoke(
        logs_cmd, ["--bundle", "--log-dir", str(log_dir), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.exists()

    with tarfile.open(out) as tar:
        names = tar.getnames()
        assert "rodeo-bundle/config-redacted.yaml" in names
        assert "rodeo-bundle/state.yaml" in names
        assert "rodeo-bundle/harvester1_serial.tail.log" in names
        cfg_text = tar.extractfile("rodeo-bundle/config-redacted.yaml").read().decode()
        assert "REDACTED" in cfg_text
        assert "Foobar" not in cfg_text


def test_bundle_works_without_logs_or_state(tmp_path):
    out = tmp_path / "bundle.tar.gz"
    result = CliRunner().invoke(
        logs_cmd, ["--bundle", "--log-dir", str(tmp_path / "nope"), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.exists()


def test_logs_rejects_unknown_vm(tmp_path):
    result = CliRunner().invoke(logs_cmd, ["bogus-vm"])
    assert result.exit_code == 1
