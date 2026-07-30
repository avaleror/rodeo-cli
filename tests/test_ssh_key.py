"""Tests for managed ~/.rodeo/ssh identity."""
from __future__ import annotations

import pytest

from rodeo.config import ConfigError
from rodeo.ssh_key import (
    DEFAULT_EC2_KEY_NAME,
    ensure_ec2_key_pair,
    ensure_rodeo_ssh_key,
    local_pubkey_sha256,
    resolve_ssh_identity,
    rodeo_ssh_private_key_path,
)


@pytest.fixture
def ssh_home(tmp_path, monkeypatch):
    ssh_dir = tmp_path / "ssh"
    monkeypatch.setattr("rodeo.paths.rodeo_ssh_dir", lambda: ssh_dir)
    monkeypatch.setattr("rodeo.ssh_key.rodeo_ssh_dir", lambda: ssh_dir)
    return ssh_dir


def test_ensure_rodeo_ssh_key_idempotent(ssh_home):
    a = ensure_rodeo_ssh_key()
    b = ensure_rodeo_ssh_key()
    assert a == b
    assert a.is_file()
    assert a.parent == ssh_home
    assert rodeo_ssh_private_key_path().with_name("id_ed25519.pub").is_file()
    assert a.read_bytes() == b.read_bytes()


def test_resolve_ssh_identity_ignores_byo(ssh_home):
    path = resolve_ssh_identity("/tmp/someone-elses.pem")
    assert path == str(ensure_rodeo_ssh_key())


def test_ensure_ec2_key_pair_imports_once(ssh_home):
    class Fake:
        def __init__(self) -> None:
            self.pairs: dict = {}
            self.imports = 0

        def describe_key_pairs(self, KeyNames=None):
            names = KeyNames or []
            pairs = [self.pairs[n] for n in names if n in self.pairs]
            if names and not pairs:
                err = Exception("InvalidKeyPair.NotFound")
                err.response = {"Error": {"Code": "InvalidKeyPair.NotFound"}}
                raise err
            return {"KeyPairs": pairs}

        def import_key_pair(self, KeyName, PublicKeyMaterial):
            self.imports += 1
            fp = local_pubkey_sha256()
            self.pairs[KeyName] = {"KeyName": KeyName, "KeyFingerprint": fp}
            return self.pairs[KeyName]

    fake = Fake()
    assert ensure_ec2_key_pair(fake) == DEFAULT_EC2_KEY_NAME
    assert fake.imports == 1
    assert ensure_ec2_key_pair(fake) == DEFAULT_EC2_KEY_NAME
    assert fake.imports == 1


def test_ensure_ec2_key_pair_mismatch(ssh_home):
    class Fake:
        def describe_key_pairs(self, KeyNames=None):
            return {
                "KeyPairs": [
                    {"KeyName": "rodeo", "KeyFingerprint": "SHA256:not-the-local-key"}
                ]
            }

    with pytest.raises(ConfigError, match="fingerprint"):
        ensure_ec2_key_pair(Fake())


def test_build_ec2_userdata_passwordless_root(ssh_home):
    from rodeo.ssh_key import build_ec2_userdata, ensure_rodeo_ssh_key, rodeo_ssh_public_key_path

    ensure_rodeo_ssh_key()
    pub = rodeo_ssh_public_key_path().read_text().strip()
    ud = build_ec2_userdata(ssh_user="ec2-user")
    assert ud.startswith("#cloud-config")
    assert "NOPASSWD:ALL" in ud
    assert "PermitRootLogin prohibit-password" in ud
    assert "/etc/sudoers.d/90-rodeo" in ud
    assert "/root/.ssh/authorized_keys" in ud
    assert pub in ud
    assert "ec2-user" in ud
