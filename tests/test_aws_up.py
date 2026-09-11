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
from rodeo.install_source import install_url_for_ref, resolve_install_source
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


def test_remote_up_script_keeps_the_aws_target():
    """The remote must run --target aws, not --target baremetal.

    This test previously asserted the opposite. On EC2, up_cmd detects IMDS and
    takes the local-deploy path either way, but only "aws" keeps
    deployment_target: aws in the plan — which is what makes apply_host_context
    raise disk_gb to the aws floor and set storage.backend: nvme so kvm_host
    mounts the instance store. Phase behaviour is baremetal regardless, via
    up_cmd's local_target.

    With baremetal the remote seeded a plain baremetal lab (disk_gb 250) on the
    10 GB root EBS and preflight failed — "need ~520 GB, have 7 GB free" — with
    a 3.4 TB NVMe sitting unmounted beside it.
    """
    script = remote_up_script(lab_dir="/root/lab", profile="harvester")
    assert "--target aws" in script
    assert "--target baremetal" not in script
    assert "--profile harvester" in script
    assert "install.sh" in script
    assert "PIPESTATUS[0]" in script
    assert "AWS_UP_EXIT:$ec" in script


def test_remote_up_script_creates_the_log_dir_before_teeing():
    """The pipeline tees into $HOME/.rodeo/logs, which does not exist on a fresh
    host — rodeo creates it on first run, and that run is the one being logged.
    Without the mkdir, tee exits immediately and rodeo dies on SIGPIPE before
    doing any work."""
    script = remote_up_script(lab_dir="/root/lab", profile="test")
    assert 'mkdir -p "$HOME/.rodeo/logs"' in script
    assert script.index('mkdir -p "$HOME/.rodeo/logs"') < script.index("tee -a")


def test_remote_up_script_without_a_ref_leaves_an_existing_install_alone():
    """No ref = no version change. Same stance as `clean --refresh`: never move
    a pinned host's rodeo out from under it unasked."""
    script = remote_up_script(lab_dir="/root/lab", profile="test")
    assert "if ! command -v rodeo >/dev/null 2>&1; then" in script
    assert "--ref" not in script


def test_remote_up_script_with_a_ref_always_reinstalls():
    """A ref must reach an *already bootstrapped* host.

    The bootstrap used to be guarded by `command -v rodeo`, so a host
    provisioned before a commit kept running the code it was first installed
    with — pushing to main changed nothing, and the deploy silently tested
    stale code. With a ref the installer runs unconditionally and install.sh
    hard-resets the checkout.
    """
    script = remote_up_script(lab_dir="/root/lab", profile="test", ref="feat/x")
    assert "bash -s -- --ref feat/x" in script
    assert "command -v rodeo >/dev/null 2>&1" not in script
    # The install must still be verified before rodeo is invoked.
    assert script.index("--ref feat/x") < script.index("command -v rodeo >/dev/null")


def test_ref_is_shell_quoted():
    script = remote_up_script(lab_dir="/root/lab", profile="test", ref="v0.15.0")
    assert "; rm -rf" not in script
    with pytest.raises(ConfigError, match="invalid rodeo-cli ref"):
        resolve_install_source({"type": "aws"}, ref="main; rm -rf /")


@pytest.mark.parametrize("bad", ["", "  ", "-x", "a..b", "feat/../../etc"])
def test_resolve_install_source_rejects_hostile_refs(bad):
    if not bad.strip():
        # Blank is "no ref", not an error.
        assert resolve_install_source({"type": "aws"}, ref=bad)[1] is None
        return
    with pytest.raises(ConfigError):
        resolve_install_source({"type": "aws"}, ref=bad)


def test_installer_is_fetched_from_the_same_ref_it_installs():
    """install.sh and the code it checks out must not disagree — fetching the
    installer from main while checking out a branch reintroduces the bug in a
    subtler form."""
    url, ref = resolve_install_source({"type": "aws"}, ref="feat/x")
    assert ref == "feat/x"
    assert url == install_url_for_ref("feat/x")
    assert "/feat/x/install.sh" in url


def test_explicit_install_url_wins_over_the_ref():
    """A configured install_url means a fork or an air-gapped mirror; the ref
    still gets passed to it."""
    url, ref = resolve_install_source(
        {"type": "aws", "install_url": "https://mirror.internal/install.sh"},
        ref="v0.15.0",
    )
    assert url == "https://mirror.internal/install.sh"
    assert ref == "v0.15.0"


def test_provider_ref_is_honoured_and_the_flag_beats_it():
    assert resolve_install_source({"type": "aws", "ref": "v0.15.0"})[1] == "v0.15.0"
    assert resolve_install_source({"type": "aws", "ref": "v0.15.0"}, ref="main")[1] == "main"


def test_execute_aws_up_passes_the_ref_to_the_remote_script(monkeypatch, tmp_path):
    """End of the plumbing: --ref must actually land in the SSH command."""
    from rodeo.providers import remote_up as mod

    seen: dict[str, object] = {}

    def _fake_run_remote(inv, fh, argv, timeout=None):
        seen["argv"] = argv
        return type("R", (), {"ok": True, "rc": 0, "stdout": "AWS_UP_EXIT:0\n", "stderr": ""})()

    monkeypatch.setattr(mod, "run_remote", _fake_run_remote)
    monkeypatch.setattr(mod, "rodeo_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(mod, "resolve_ssh_identity", lambda p: str(tmp_path / "id_ed25519"))

    host = ProvisionedHost(
        id=SINGLE_HOST_ID,
        ssh={"host": "1.2.3.4", "user": "ec2-user"},
        public_ip="1.2.3.4",
        provider_id="i-0abc",
        labels={},
    )
    mod.run_remote_up({"name": "lab", "provider": {"type": "aws"}}, host, ref="feat/x")
    argv = seen["argv"]
    assert argv[:4] == ["sudo", "-n", "bash", "-lc"]
    assert "--ref feat/x" in argv[4]


def test_a_bad_ref_fails_before_anything_is_provisioned(tmp_path):
    """An i7i costs $1.70/hr. A typo'd ref must be rejected before launch, not
    after the instance is up and billing."""
    lab = _aws_plan(tmp_path)
    cfg = load_config(config_dir=str(lab))
    called: list[str] = []

    def _boom(name):
        called.append(name)
        raise AssertionError("provider must not be reached with an invalid ref")

    with pytest.raises(ConfigError, match="invalid rodeo-cli ref"):
        execute_aws_up(cfg, profile="test", ref="not a ref", get_provider_fn=_boom)
    assert called == []


def test_up_cmd_exposes_ref():
    from rodeo.commands.up_cmd import up_cmd

    params = {p.name for p in up_cmd.params}
    assert "ref" in params, "rodeo up must expose --ref for the AWS remote bootstrap"


def test_on_ec2_imdsv2(monkeypatch):
    from rodeo.providers import remote_up as mod

    calls: list[str] = []

    class _Resp:
        def __init__(self, body: bytes = b"", status: int = 200):
            self._body = body
            self.status = status

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        calls.append(url)
        if url.endswith("/api/token"):
            return _Resp(b"tok-123")
        if "meta-data" in url:
            assert req.headers.get("X-aws-ec2-metadata-token") == "tok-123"
            return _Resp(b"ami-id\n")
        raise AssertionError(url)

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod, "_on_ec2_dmi", lambda: False)
    assert mod.on_ec2(timeout=0.1) is True
    assert any("/api/token" in c for c in calls)


def test_on_ec2_dmi_fallback(monkeypatch):
    from pathlib import Path

    from rodeo.providers import remote_up as mod

    monkeypatch.setattr(mod, "_on_ec2_imds", lambda timeout=0.4: False)

    def fake_read(self, *a, **k):
        if str(self).endswith("product_uuid"):
            return "EC2deadbeef\n"
        raise OSError("missing")

    monkeypatch.setattr(Path, "read_text", fake_read)
    assert mod.on_ec2() is True


def test_provision_primary_and_execute(tmp_path, monkeypatch):
    lab = _aws_plan(tmp_path)
    cfg = load_config(config_dir=str(lab))
    validate_config(cfg)
    fake = _FakeEC2()
    provider = AwsHostProvider(ec2_client=fake, sleep=lambda s: None)
    ssh_dir = tmp_path / "managed-ssh"
    monkeypatch.setattr("rodeo.paths.rodeo_ssh_dir", lambda: ssh_dir)
    monkeypatch.setattr("rodeo.ssh_key.rodeo_ssh_dir", lambda: ssh_dir)
    monkeypatch.setattr(
        "rodeo.providers.aws.AwsHostProvider._wait_ssh",
        lambda *a, **k: None,
    )
    monkeypatch.setattr("rodeo.providers.aws.plant_rodeo_ssh_key", lambda *a, **k: None)
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
    ssh_dir = tmp_path / "managed-ssh"
    monkeypatch.setattr("rodeo.paths.rodeo_ssh_dir", lambda: ssh_dir)
    monkeypatch.setattr("rodeo.ssh_key.rodeo_ssh_dir", lambda: ssh_dir)
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
    monkeypatch.setattr("rodeo.providers.aws.plant_rodeo_ssh_key", lambda *a, **k: None)
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


def test_aws_target_needs_no_provider_block_when_on_ec2(monkeypatch):
    """On the EC2 host itself, deployment_target: aws must validate without a
    provider: block.

    The remote deploy keeps target aws so host_context applies the aws
    adaptation (disk_gb floor, storage.backend: nvme). It has no provider block
    — the laptop holds that — and demanding one made the remote deploy fail
    with "deployment_target: aws requires a provider: block" on a host that was
    already provisioned and had nothing left to acquire.
    """
    from rodeo import config as cfgmod

    monkeypatch.setattr("rodeo.providers.remote_up.on_ec2", lambda **_k: True)
    cfgmod._validate_aws_provider({"deployment_target": "aws"})  # must not raise

    # Off EC2 (the laptop control plane) it is still required.
    monkeypatch.setattr("rodeo.providers.remote_up.on_ec2", lambda **_k: False)
    with pytest.raises(cfgmod.ConfigError, match="requires a provider"):
        cfgmod._validate_aws_provider({"deployment_target": "aws"})
