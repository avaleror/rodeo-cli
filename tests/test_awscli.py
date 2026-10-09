"""AWS CLI install and credential checks for plans that deploy into AWS (rodeo/awscli.py)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from rodeo import awscli

FPR = awscli.KEY_FINGERPRINT


def _liab_cfg(**settings):
    return {"type": "lab-in-a-box", "name": "bake",
            "lab_in_a_box": {"cloud": {"cloudtype": "aws", "account": "bake",
                                       "settings": {"AWS_REGION": "eu-north-1", **settings}}}}


class Done:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


@pytest.fixture()
def no_aws(monkeypatch, tmp_path):
    """No aws CLI anywhere: not on PATH, not in /usr/local/bin."""
    monkeypatch.setattr(awscli, "LOCAL_BIN", tmp_path / "bin")
    real_which = shutil.which
    monkeypatch.setattr(awscli.shutil, "which", lambda name: None if name == "aws" else real_which(name))
    return tmp_path / "bin"


def test_detects_plans_that_deploy_into_aws():
    assert awscli.labinabox_aws(_liab_cfg(AWS_PROFILE="p"))["cloudtype"] == "aws"
    assert awscli.deploys_to_aws(_liab_cfg(AWS_PROFILE="p"))
    assert awscli.deploys_to_aws({"deployment_target": "aws", "provider": {"type": "aws", "region": "x"}})
    assert not awscli.deploys_to_aws({"type": "rancher", "deployment_target": "baremetal"})
    assert not awscli.deploys_to_aws({"type": "lab-in-a-box", "lab_in_a_box": {}})


def test_cli_env_uses_only_the_plans_credentials():
    host = {"PATH": "/bin", "AWS_PROFILE": "mine", "AWS_ACCESS_KEY_ID": "AKIAHOST", "AWS_REGION": "us-east-1"}
    env = awscli.cli_env({"AWS_REGION": "eu-north-1", "AWS_PROFILE": "bake", "AWS_ACCESS_KEY_ID": "AKIAPLAN"}, host)
    assert env == {"PATH": "/bin", "AWS_REGION": "eu-north-1", "AWS_DEFAULT_REGION": "eu-north-1", "AWS_PROFILE": "bake"}
    env = awscli.cli_env({"AWS_REGION": "eu-north-1", "AWS_ACCESS_KEY_ID": "ASIAX", "AWS_SECRET_ACCESS_KEY": "s",
                          "AWS_SESSION_TOKEN": "t"}, host)
    assert env["AWS_ACCESS_KEY_ID"] == "ASIAX" and env["AWS_SESSION_TOKEN"] == "t" and "AWS_PROFILE" not in env


def test_credentials_are_required(no_aws):
    ok, detail = awscli.check_credentials(_liab_cfg())
    assert not ok and "no AWS credentials" in detail and "aws_access_key_id" in detail
    ok, detail = awscli.check_credentials(_liab_cfg(AWS_PROFILE="bake"))
    assert not ok and "install-deps --aws" in detail
    assert awscli.check_credentials({"type": "rancher"}) == (True, "not deploying into AWS")


def test_credentials_are_checked_with_sts(monkeypatch, tmp_path):
    aws = tmp_path / "aws"
    aws.write_text("#!/bin/sh\n")
    aws.chmod(0o755)
    monkeypatch.setattr(awscli, "ensure_on_path", lambda: str(aws))
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return Done(0, "arn:aws:sts::1:assumed-role/x/me\n")

    assert awscli.check_credentials(_liab_cfg(AWS_PROFILE="bake"), run=run) == (True, "arn:aws:sts::1:assumed-role/x/me")
    assert seen["cmd"][1:3] == ["sts", "get-caller-identity"] and seen["env"]["AWS_PROFILE"] == "bake"
    expired = awscli.check_credentials(_liab_cfg(AWS_PROFILE="bake"),
                                       run=lambda *a, **k: Done(255, err="Token has expired and refresh failed"))
    assert not expired[0] and "Token has expired" in expired[1] and "aws sso login --profile bake" in expired[1]
    keys = awscli.check_credentials(_liab_cfg(AWS_ACCESS_KEY_ID="A", AWS_SECRET_ACCESS_KEY="S"),
                                    run=lambda *a, **k: Done(255, err="InvalidClientTokenId"))
    assert not keys[0] and "aws_session_token" in keys[1]


def test_ensure_on_path_adds_usr_local_bin(monkeypatch, no_aws):
    no_aws.mkdir()
    (no_aws / "aws").write_text("#!/bin/sh\n")
    (no_aws / "aws").chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin")
    assert awscli.ensure_on_path() == str(no_aws / "aws")
    assert awscli.os.environ["PATH"].split(":")[0] == str(no_aws)


def test_signature_status_must_name_the_aws_key():
    good = "[GNUPG:] GOODSIG A6310ACC4672475C AWS CLI Team\n[GNUPG:] VALIDSIG X 2024-01-01 1 0 4 0 1 8 00 " + FPR
    expired = "[GNUPG:] EXPKEYSIG A6310ACC4672475C AWS CLI Team\n[GNUPG:] VALIDSIG X 2024 1 0 4 0 1 8 00 " + FPR
    assert awscli.verified(good) and awscli.verified(expired)
    assert not awscli.verified("[GNUPG:] BADSIG A6310ACC4672475C AWS CLI Team")
    assert not awscli.verified("[GNUPG:] VALIDSIG X 2024 1 0 4 0 1 8 00 " + "0" * 40)
    assert not awscli.verified("")


def test_installer_url_per_architecture():
    assert awscli.installer_url("x86_64").endswith("awscli-exe-linux-x86_64.zip")
    assert awscli.installer_url("arm64").endswith("awscli-exe-linux-aarch64.zip")
    with pytest.raises(RuntimeError):
        awscli.installer_url("s390x")


def _fake_install(no_aws, status):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "--verify" in cmd:
            return Done(0, status)
        if cmd[-1] == "--update":
            no_aws.mkdir(exist_ok=True)
            (no_aws / "aws").write_text("#!/bin/sh\n")
            (no_aws / "aws").chmod(0o755)
        if cmd[-1] == "--version":
            return Done(0, "aws-cli/2.0.0 Python/3")
        return Done(0)
    return calls, run


def test_install_cli_checks_the_signature_before_installing(no_aws, monkeypatch):
    real_which = awscli.shutil.which
    monkeypatch.setattr(awscli.shutil, "which", lambda n: None if n == "aws" else (real_which(n) or "/usr/bin/" + n))
    calls, run = _fake_install(no_aws, "[GNUPG:] VALIDSIG X 2024 1 0 4 0 1 8 00 " + FPR)
    assert awscli.install_cli(run=run, machine="x86_64") == "aws-cli/2.0.0 Python/3"
    names = [Path(c[0]).name for c in calls]
    assert names == ["curl", "curl", "gpg", "gpg", "unzip", "install", "aws"]
    assert all("-4" in c for c in calls if c[0] == "curl")
    assert str(awscli.KEY_FILE) in calls[2]

    calls, run = _fake_install(no_aws.parent / "bin2", "[GNUPG:] BADSIG A6310ACC4672475C x")
    with pytest.raises(RuntimeError, match="signature"):
        awscli.install_cli(run=run, machine="x86_64")
    assert not any(c[-1] == "--update" for c in calls)


@pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg is not installed")
def test_bundled_key_is_the_aws_cli_key(tmp_path):
    home = tmp_path / "g"
    home.mkdir(mode=0o700)
    subprocess.run(["gpg", "--batch", "--homedir", str(home), "--import", str(awscli.KEY_FILE)],
                   check=True, capture_output=True)
    out = subprocess.run(["gpg", "--batch", "--homedir", str(home), "--with-colons", "--fingerprint"],
                         check=True, capture_output=True, text=True).stdout
    assert "fpr:::::::::{}:".format(FPR) in out


def test_preflight_requires_cli_and_credentials(monkeypatch, tmp_path, capsys):
    from rodeo import preflight

    monkeypatch.setattr(awscli, "ensure_on_path", lambda: None)
    monkeypatch.setattr(awscli, "check_credentials", lambda cfg: (False, "Token has expired"))
    assert preflight.run_preflight(_liab_cfg(AWS_PROFILE="bake"), tmp_path) is False
    out = capsys.readouterr().out
    assert "install-deps --aws" in out and "Token has expired" in out


def test_up_offers_the_cli_only_when_needed(monkeypatch):
    from rodeo.commands import install_deps, up_cmd

    installed = []
    monkeypatch.setattr(install_deps, "install_aws_cli", lambda distro: installed.append(distro))
    monkeypatch.setattr(awscli, "ensure_on_path", lambda: None)
    up_cmd._ensure_aws_cli({"type": "rancher"}, assume_yes=True)
    assert installed == []
    up_cmd._ensure_aws_cli(_liab_cfg(AWS_PROFILE="bake"), assume_yes=True)
    assert len(installed) == 1
