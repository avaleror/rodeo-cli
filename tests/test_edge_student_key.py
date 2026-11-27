"""suse-edge edge3/edge4 trust the lab player's student key when it exists."""
from __future__ import annotations

from rodeo.engine.rancher.content import student_vm_pubkey


def test_student_pubkey_is_type_and_key_only(tmp_path):
    pub = tmp_path / "k.pub"
    pub.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB+x/y= student@lab-vms\n")
    assert student_vm_pubkey(pub) == "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB+x/y="


def test_no_or_odd_key_means_none(tmp_path):
    assert student_vm_pubkey(tmp_path / "missing.pub") == ""
    odd = tmp_path / "odd.pub"
    odd.write_text('ssh-ed25519 AAAA"; rm -rf / #\n')
    assert student_vm_pubkey(odd) == "ssh-ed25519 AAAA"
