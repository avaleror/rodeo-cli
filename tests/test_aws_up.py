"""Tests for deployment_target: aws and single-host remote_up orchestration."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from rodeo.config import ConfigError, load_config, validate_config
from rodeo.providers.aws import AwsHostProvider
from rodeo.providers.base import SINGLE_HOST_ID, ProvisionedHost
from rodeo.providers.remote_up import (
    destroy_primary,
    execute_aws_up,
    provision_primary,
    remote_up_script,
    save_aws_host_state,
)
from tests.test_providers_aws import _FakeEC2


def _aws_plan(tmp_path: Path) -> Path:
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "rodeo-plan.yaml").write_text(
        textwrap.dedent(
            """
            type: suse-virt
            name: aws-demo
            deployment_target: aws
            credentials:
              harvester_os_password: "secret"
              harvester_admin_password: "secret"
              rancher_admin_password: "secret"
              harvester_token: "token-token-token"
            provider:
              type: aws
              region: eu-central-1
              instance_type: m7i.metal-24xl
              ami: ami-abc
              key_name: rodeo
              subnet_id: subnet-1
              security_group_ids: [sg-1]
              identity_file: /tmp/key.pem
              ssh_user: ec2-user
            """
        )
    )
    return lab


def test_validate_aws_requires_provider(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "rodeo-plan.yaml").write_text(
        textwrap.dedent(
            """
            type: suse-virt
            name: x
            deployment_target: aws
            credentials:
              harvester_os_password: "s"
              harvester_admin_password: "s"
              rancher_admin_password: "s"
              harvester_token: "tok"
            """
        )
    )
    cfg = load_config(config_dir=str(lab))
    with pytest.raises(ConfigError, match="provider:"):
        validate_config(cfg)


def test_validate_aws_ok(tmp_path):
    lab = _aws_plan(tmp_path)
    cfg = load_config(config_dir=str(lab))
    validate_config(cfg)


def test_remote_up_script_uses_baremetal():
    script = remote_up_script(lab_dir="/root/lab", profile="harvester")
    assert "--target baremetal" in script
    assert "--profile harvester" in script
    assert "install.sh" in script


def test_provision_primary_and_execute(tmp_path, monkeypatch):
    lab = _aws_plan(tmp_path)
    cfg = load_config(config_dir=str(lab))
    validate_config(cfg)
    fake = _FakeEC2()
    provider = AwsHostProvider(ec2_client=fake, sleep=lambda s: None)
    monkeypatch.setattr(
        "rodeo.providers.aws.AwsHostProvider._wait_ssh",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "rodeo.providers.remote_up.run_remote",
        lambda *a, **k: type(
            "R",
            (),
            {"ok": True, "rc": 0, "stdout": "AWS_UP_EXIT:0\n", "stderr": ""},
        )(),
    )
    # state dir under tmp
    monkeypatch.setattr(
        "rodeo.providers.remote_up.rodeo_state_dir",
        lambda: tmp_path / "state",
    )

    host = execute_aws_up(
        cfg,
        profile="harvester",
        get_provider_fn=lambda name: provider,
    )
    assert host.id == SINGLE_HOST_ID
    assert host.provider_id == "i-00000001"
    assert fake.instances

    # destroy
    results = destroy_primary(cfg, get_provider_fn=lambda name: provider)
    assert results[0].ok
    assert fake.terminated == ["i-00000001"]


def test_destroy_cli_requires_cloud_flag(tmp_path):
    lab = _aws_plan(tmp_path)
    from rodeo.cli import cli

    result = CliRunner().invoke(cli, ["destroy", "--config-dir", str(lab)])
    assert result.exit_code == 2
    assert "--cloud" in result.output


def test_destroy_cli_yes(tmp_path, monkeypatch):
    lab = _aws_plan(tmp_path)
    fake = _FakeEC2()
    provider = AwsHostProvider(ec2_client=fake, sleep=lambda s: None)
    monkeypatch.setattr(
        "rodeo.providers.remote_up.get_provider",
        lambda name: provider,
    )
    # seed a tagged instance via provision
    cfg = load_config(config_dir=str(lab))
    monkeypatch.setattr(
        "rodeo.providers.aws.AwsHostProvider._wait_ssh",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "rodeo.providers.remote_up.rodeo_state_dir",
        lambda: tmp_path / "state",
    )
    provision_primary(cfg, get_provider_fn=lambda name: provider)

    from rodeo.cli import cli

    result = CliRunner().invoke(
        cli,
        ["destroy", "--cloud", "--yes", "--config-dir", str(lab)],
    )
    assert result.exit_code == 0, result.output
    assert fake.terminated == ["i-00000001"]


def test_save_aws_host_state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "rodeo.providers.remote_up.rodeo_state_dir",
        lambda: tmp_path / "state",
    )
    host = ProvisionedHost(
        id="primary",
        ssh="1.2.3.4",
        public_ip="1.2.3.4",
        provider_id="i-abc",
        labels={"provider": "aws"},
    )
    path = save_aws_host_state("aws-demo", host)
    data = yaml.safe_load(path.read_text())
    assert data["provider_id"] == "i-abc"
    assert data["public_ip"] == "1.2.3.4"
