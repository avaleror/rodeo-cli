"""rodeo init — secrets generation."""
from __future__ import annotations

import stat

from click.testing import CliRunner

from rodeo.commands.init_cmd import _random_password, init_cmd


def test_random_password_meets_complexity():
    for _ in range(20):
        pw = _random_password()
        assert len(pw) >= 12
        assert any(c.isdigit() for c in pw)
        assert any(c.isupper() for c in pw)
        assert any(c.islower() for c in pw)


def test_init_writes_random_secrets(tmp_path):
    result = CliRunner().invoke(init_cmd, [str(tmp_path / "work")])
    assert result.exit_code == 0, result.output

    secrets = tmp_path / ".rodeo" / "secrets.yaml"  # HOME is tmp_path (conftest)
    assert secrets.exists()
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    content = secrets.read_text()
    assert "Foobar" not in content and "CHANGE_ME" not in content
    assert "harvester_token:" in content
    # Regression: init_cmd used to hand-roll this file (drifted from secretgen.py
    # and never wrote rancher_vm_password, so `??rancher_vm_password` in the
    # suse-edge profile's plan failed closed on every fresh init).
    assert "rancher_vm_password:" in content
    env_content = (tmp_path / "work" / "rodeo-secrets.env").read_text()
    assert "RANCHER_VM_PASSWORD=" in env_content
    assert (tmp_path / "work" / "rodeo-plan.yaml").exists()


def test_init_uses_env_password(tmp_path, monkeypatch):
    monkeypatch.setenv("RODEO_PASSWORD", "EnvPassword12345")
    result = CliRunner().invoke(init_cmd, [str(tmp_path / "work")])
    assert result.exit_code == 0, result.output
    content = (tmp_path / ".rodeo" / "secrets.yaml").read_text()
    assert 'harvester_os_password: "EnvPassword12345"' in content


def test_init_rejects_short_env_password(tmp_path, monkeypatch):
    monkeypatch.setenv("RODEO_PASSWORD", "short")
    result = CliRunner().invoke(init_cmd, [str(tmp_path / "work")])
    assert result.exit_code == 1
    assert not (tmp_path / ".rodeo" / "secrets.yaml").exists()


def test_init_ask_prompts_hidden(tmp_path, monkeypatch):
    monkeypatch.delenv("RODEO_PASSWORD", raising=False)
    result = CliRunner().invoke(
        init_cmd,
        ["--ask", str(tmp_path / "work")],
        input="PromptedPw12345\nPromptedPw12345\n",
    )
    assert result.exit_code == 0, result.output
    content = (tmp_path / ".rodeo" / "secrets.yaml").read_text()
    assert 'harvester_os_password: "PromptedPw12345"' in content  # gitleaks:allow
    assert "PromptedPw12345" not in result.output  # hidden input is not echoed


def test_init_ask_rejects_short_password(tmp_path):
    result = CliRunner().invoke(
        init_cmd,
        ["--ask", str(tmp_path / "work")],
        input="short\nshort\n",
    )
    assert result.exit_code == 1
    assert not (tmp_path / ".rodeo" / "secrets.yaml").exists()


def test_init_does_not_overwrite_without_force(tmp_path):
    runner = CliRunner()
    runner.invoke(init_cmd, [str(tmp_path / "work")])
    secrets = tmp_path / ".rodeo" / "secrets.yaml"
    first = secrets.read_text()
    runner.invoke(init_cmd, [str(tmp_path / "work")])
    assert secrets.read_text() == first
