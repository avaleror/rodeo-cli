"""`rodeo logins add|remove|list` (rodeo/commands/logins_cmd.py) on the builder's login file."""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from rodeo.builder import login
from rodeo.commands.logins_cmd import logins_cmd


@pytest.fixture(autouse=True)
def fast_hashes(monkeypatch, tmp_path):
    monkeypatch.setattr(login, "BCRYPT_ROUNDS", 4)
    monkeypatch.setattr(login, "SYSTEM_LOGIN_FILE", tmp_path / "no-system-file")


def test_add_list_and_remove_in_the_users_file(tmp_path):
    runner = CliRunner()
    r = runner.invoke(logins_cmd, ["add", "alice"], input="alicepass\nalicepass\n")
    assert r.exit_code == 0, r.output
    users = tmp_path / ".rodeo" / "builder" / "htpasswd"
    assert str(users) in r.output and users.is_file()
    assert login.verify("alice", "alicepass") is True
    r = runner.invoke(logins_cmd, ["add", "bob", "--password-stdin"], input="bobpass12\n")
    assert r.exit_code == 0, r.output
    r = runner.invoke(logins_cmd, ["list"])
    assert r.exit_code == 0 and "alice" in r.output and "bob" in r.output and "(" not in r.output
    r = runner.invoke(logins_cmd, ["remove", "bob"])
    assert r.exit_code == 0 and "Removed bob" in r.output
    assert [u for u, _ in login.list_users()] == ["alice"]
    r = runner.invoke(logins_cmd, ["remove", "bob"])
    assert r.exit_code == 1 and "bob is not in" in r.output


def test_add_refuses_bad_passwords_and_names():
    runner = CliRunner()
    r = runner.invoke(logins_cmd, ["add", "alice", "--password-stdin"], input="short\n")
    assert r.exit_code == 1 and "at least 8" in r.output
    r = runner.invoke(logins_cmd, ["add", "bad user", "--password-stdin"], input="alicepass\n")
    assert r.exit_code == 1 and "user name" in r.output
    assert login.list_users() == []


def test_file_option_and_old_hashes_are_pointed_out_and_converted(tmp_path):
    other = tmp_path / "other"
    other.write_text("legacy:$apr1$saltsalt$dSONY5u48qptEo.I0ySla0\nodd:{SHA}W6ph5Mm5Pz8GgiULbPgzG37mj9g=\n")
    runner = CliRunner()
    r = runner.invoke(logins_cmd, ["add", "alice", "--file", str(other), "--password-stdin"], input="alicepass\n")
    assert r.exit_code == 0, r.output
    r = runner.invoke(logins_cmd, ["list", "--file", str(other)])
    assert r.exit_code == 0 and "legacy  (APR1-MD5: set a new password" in r.output and "odd  (a hash rodeo cannot check" in r.output
    assert login.verify("legacy", "alicepass", other) is True
    r = runner.invoke(logins_cmd, ["add", "legacy", "--file", str(other), "--password-stdin"], input="newpass123\n")
    assert r.exit_code == 0, r.output
    assert login.list_users(other) == [("legacy", "bcrypt"), ("odd", "other"), ("alice", "bcrypt")]
    r = runner.invoke(logins_cmd, ["list"])
    assert "no users yet" in r.output


def test_a_slow_hash_prints_a_waiting_note(monkeypatch):
    import time

    from rodeo.commands import logins_cmd as cmd

    monkeypatch.setattr(cmd, "WAIT_AFTER", 0.01)
    real = login.hash_password

    def slow(password):
        time.sleep(0.05)
        return real(password)

    monkeypatch.setattr(login, "hash_password", slow)
    r = CliRunner().invoke(logins_cmd, ["add", "alice", "--password-stdin"], input="alicepass\n")
    assert r.exit_code == 0, r.output
    assert "waiting (hashing the password)..." in r.stderr and "Added alice" in r.output
    monkeypatch.setattr(cmd, "WAIT_AFTER", 5.0)
    r = CliRunner().invoke(logins_cmd, ["add", "bob", "--password-stdin"], input="bobpass12\n")
    assert r.exit_code == 0 and r.stderr == ""
