"""Shared secret generation helpers."""
from __future__ import annotations

import stat

from rodeo import secretgen


def test_random_password_complexity():
    for _ in range(20):
        pw = secretgen.random_password()
        assert len(pw) >= 12
        assert any(c.isdigit() for c in pw)
        assert any(c.isupper() for c in pw)
        assert any(c.islower() for c in pw)


def test_ensure_secrets_file_creates_then_reuses(tmp_path):
    path = tmp_path / ".rodeo" / "secrets.yaml"
    pw1, tok1, created1 = secretgen.ensure_secrets_file(path)
    assert created1 is True
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    pw2, tok2, created2 = secretgen.ensure_secrets_file(path)
    assert created2 is False
    assert (pw2, tok2) == (pw1, tok1)


def test_read_secrets_file_roundtrip(tmp_path):
    path = tmp_path / "secrets.yaml"
    secretgen.write_secrets_file(path, "Password1234", "tok-abc")  # gitleaks:allow
    pw, tok = secretgen.read_secrets_file(path)
    assert pw == "Password1234"  # gitleaks:allow
    assert tok == "tok-abc"


def test_read_secrets_file_missing(tmp_path):
    assert secretgen.read_secrets_file(tmp_path / "nope.yaml") == (None, None)


def test_write_secrets_file_includes_suse_edge_vm_password(tmp_path):
    """rancher_vm_password (suse-edge's Rancher/EIB VM console password) must be
    written alongside the harvester-profile keys — its absence is a fail-closed
    deploy blocker for the suse-edge profile (??rancher_vm_password never resolves)."""
    path = tmp_path / "secrets.yaml"
    secretgen.write_secrets_file(path, "Password1234", "tok-abc")  # gitleaks:allow
    text = path.read_text()
    assert 'rancher_vm_password: "Password1234"' in text  # gitleaks:allow


def test_update_admin_passwords_preserves_untouched_fields(tmp_path):
    """rodeo set-password only rotates the dashboard admin passwords — it must not
    disturb harvester_os_password, rancher_vm_password, harvester_token, or
    gitea_admin_password (write_secrets_file would silently drop the latter, since
    it doesn't know about that key at all)."""
    path = tmp_path / "secrets.yaml"
    path.write_text(
        'harvester_os_password: "OsPass1234"\n'
        'harvester_admin_password: "OldPass1234"\n'
        'rancher_admin_password: "OldPass1234"\n'
        'rancher_vm_password: "OsPass1234"\n'
        'harvester_token: "tok-abc"\n'
        'gitea_admin_password: "GiteaPass1234"\n'
    )  # gitleaks:allow

    secretgen.update_admin_passwords(path, "NewPass5678", {"harvester_admin_password", "rancher_admin_password"})  # gitleaks:allow

    text = path.read_text()
    assert 'harvester_admin_password: "NewPass5678"' in text  # gitleaks:allow
    assert 'rancher_admin_password: "NewPass5678"' in text  # gitleaks:allow
    assert 'harvester_os_password: "OsPass1234"' in text  # gitleaks:allow
    assert 'rancher_vm_password: "OsPass1234"' in text  # gitleaks:allow
    assert 'harvester_token: "tok-abc"' in text
    assert 'gitea_admin_password: "GiteaPass1234"' in text  # gitleaks:allow


def test_update_admin_passwords_scoped_to_given_keys(tmp_path):
    """--target harvester (or rancher) must leave the other service's password alone."""
    path = tmp_path / "secrets.yaml"
    path.write_text(
        'harvester_admin_password: "OldPass1234"\n'
        'rancher_admin_password: "OldPass1234"\n'
    )  # gitleaks:allow

    secretgen.update_admin_passwords(path, "NewPass5678", {"harvester_admin_password"})  # gitleaks:allow

    text = path.read_text()
    assert 'harvester_admin_password: "NewPass5678"' in text  # gitleaks:allow
    assert 'rancher_admin_password: "OldPass1234"' in text  # gitleaks:allow
