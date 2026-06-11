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
    assert (tmp_path / "work" / "rodeo-plan.yaml").exists()


def test_init_does_not_overwrite_without_force(tmp_path):
    runner = CliRunner()
    runner.invoke(init_cmd, [str(tmp_path / "work")])
    secrets = tmp_path / ".rodeo" / "secrets.yaml"
    first = secrets.read_text()
    runner.invoke(init_cmd, [str(tmp_path / "work")])
    assert secrets.read_text() == first
