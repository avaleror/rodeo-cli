"""Rodeo Builder: deploying to a KVM host over SSH (rodeo/builder/remotehost.py)."""
from __future__ import annotations

import pytest
import yaml

from rodeo.builder import remotehost
from rodeo.builder.vault import types

PLAN = "name: x\ntype: rancher\ndeployment_target: remote\n"


@pytest.fixture
def lab(tmp_path):
    (tmp_path / "rodeo-plan.yaml").write_text(PLAN)
    return tmp_path


def _plan(lab):
    return yaml.safe_load((lab / "rodeo-plan.yaml").read_text())


def test_apply_writes_the_block_and_a_private_key_only_root_can_read(lab):
    block = remotehost.apply(lab, lab / "rodeo-plan.yaml",
                             {"host": " kvm-01 ", "ssh_user": "ops", "private_key": "KEY"})
    key = lab / remotehost.KEY_FILE
    assert block == {"host": "kvm-01", "ssh_user": "ops", "identity_file": str(key)}
    assert _plan(lab)["remote_host"] == block
    assert key.stat().st_mode & 0o777 == 0o600
    # OpenSSH rejects a key without a closing newline.
    assert key.read_text() == "KEY\n"


def test_apply_keeps_a_newline_the_key_already_has(lab):
    remotehost.apply(lab, lab / "rodeo-plan.yaml", {"host": "h", "private_key": "KEY\n"})
    assert (lab / remotehost.KEY_FILE).read_text() == "KEY\n"


def test_apply_leaves_a_local_rodeo_alone(lab):
    (lab / "rodeo-plan.yaml").write_text("name: x\ntype: rancher\ndeployment_target: baremetal\n")
    assert remotehost.apply(lab, lab / "rodeo-plan.yaml", {"host": "kvm-01", "private_key": "K"}) == {}
    assert "remote_host" not in _plan(lab)
    assert not (lab / remotehost.KEY_FILE).exists()


def test_apply_asks_for_a_host_when_neither_request_nor_plan_names_one(lab):
    with pytest.raises(remotehost.NeedRemoteHost, match="KVM host credential"):
        remotehost.apply(lab, lab / "rodeo-plan.yaml", None)


def test_apply_keeps_what_the_plan_already_says(lab):
    (lab / "rodeo-plan.yaml").write_text(
        PLAN + "remote_host:\n  host: planned\n  ssh_user: planned-user\n"
        "  identity_file: /keys/id\n  lab_dir: /srv/lab\n")
    # A request that names only a host overrides the host and nothing else.
    block = remotehost.apply(lab, lab / "rodeo-plan.yaml", {"host": "kvm-02"})
    assert block == {"host": "kvm-02", "ssh_user": "planned-user",
                     "identity_file": "/keys/id", "lab_dir": "/srv/lab"}
    assert not (lab / remotehost.KEY_FILE).exists(), "no key in the request, none written"


def test_apply_refuses_a_host_it_cannot_log_in_to(lab):
    with pytest.raises(ValueError, match="no private key"):
        remotehost.apply(lab, lab / "rodeo-plan.yaml", {"host": "kvm-01"})


def test_the_builder_offers_a_kvm_host_credential_type():
    kvm = [t for t in types() if t["id"] == "kvm-host"]
    assert kvm, "the page gates the remote target on this credential type"
    fields = {f["name"]: f for f in kvm[0]["fields"]}
    assert set(fields) == {"host", "ssh_user", "private_key", "lab_dir"}
    assert fields["private_key"]["secret"] and fields["private_key"]["multiline"]
    assert not fields["host"]["optional"] and fields["ssh_user"]["optional"]
