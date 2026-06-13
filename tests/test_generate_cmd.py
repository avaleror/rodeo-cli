from __future__ import annotations

import shutil

from click.testing import CliRunner

from rodeo.commands.generate_cmd import generate_cmd


def test_generate_with_name_creates_artifacts(tmp_path, monkeypatch):
    def fake_basic():
        return {"name": "tlab", "num_harvester": 2, "include_rancher": False, "deployment_target": "baremetal", "storage_device": ""}
    monkeypatch.setattr("rodeo.commands.generate_cmd._prompt_basic", fake_basic)
    def fake_advanced(a):
        return a
    monkeypatch.setattr("rodeo.commands.generate_cmd._prompt_advanced", fake_advanced)
    result = CliRunner().invoke(generate_cmd, ["--dir", str(tmp_path), "--name", "tlab", "--advanced"])
    assert result.exit_code == 0, result.output
    lab = tmp_path / "tlab"
    assert (lab / "definition.yaml").exists()
    assert (lab / "rodeo-plan.yaml").exists()
    assert (lab / "rodeo-secrets.env").exists()


def test_generate_does_not_clobber_existing_global_secrets(tmp_path, monkeypatch):
    def fake_basic():
        return {"name": "tlab2", "num_harvester": 2, "include_rancher": False, "deployment_target": "baremetal", "storage_device": ""}
    monkeypatch.setattr("rodeo.commands.generate_cmd._prompt_basic", fake_basic)
    def fake_advanced(a):
        return a
    monkeypatch.setattr("rodeo.commands.generate_cmd._prompt_advanced", fake_advanced)
    runner = CliRunner()
    r1 = runner.invoke(generate_cmd, ["--dir", str(tmp_path), "--name", "tlab2", "--advanced"])
    assert r1.exit_code == 0, r1.output
    secrets = tmp_path / ".rodeo" / "secrets.yaml"
    assert secrets.exists()
    first = secrets.read_text()
    lab = tmp_path / "tlab2"
    if lab.exists():
        shutil.rmtree(lab)
    r2 = runner.invoke(generate_cmd, ["--dir", str(tmp_path), "--name", "tlab2", "--advanced"])
    assert r2.exit_code == 0, r2.output
    assert secrets.read_text() == first