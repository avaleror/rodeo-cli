"""AWS and GCP lab hosts wait for SLES PAYG repositories before rodeo installs.

The readiness check is run with fake ``sudo`` / ``zypper`` on PATH, so the bash
that ships is the bash under test. The fake zypper follows what SLES 16 PAYG
does on EC2: with no repo files ``zypper lr`` warns "No repositories defined"
and exits 6; with repos that are all disabled ``zypper lr -E`` prints the same
warning but exits 0, and plain ``zypper lr`` lists the disabled rows.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from rodeo.providers import aws
from rodeo.providers.aws import AwsHostProvider
from rodeo.providers.base import ProvisionSpec
from rodeo.providers.gcp import GcpHostProvider

from tests.test_providers_aws import _FakeEC2, managed_ssh  # noqa: F401

_HEADER = (
    "echo '#  | Alias    | Name     | Enabled | GPG Check | Refresh'\n"
    "echo '---+----------+----------+---------+-----------+--------'\n"
)
_ZYPPER = {
    "none": "echo 'Warning: No repositories defined.' >&2\nexit 6\n",
    "all-disabled": (
        'for a in "$@"; do [[ $a == -E ]] && { '
        "echo 'Warning: No repositories defined.' >&2; exit 0; }; done\n"
        + _HEADER
        + "echo ' 1 | repo-oss | repo-oss | No      | ----      | ----'\n"
    ),
    "enabled": _HEADER + "echo ' 1 | repo-oss | repo-oss | Yes     | (r ) Yes  | Yes'\n",
}


def _ready(tmp_path: Path, check: str, state: str) -> bool:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sudo").write_text('#!/bin/bash\n[[ $1 == -n ]] && shift\nexec "$@"\n')
    (bin_dir / "zypper").write_text("#!/bin/bash\n" + _ZYPPER[state])
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}
    r = subprocess.run(["bash", "-c", check], env=env, capture_output=True, text=True, timeout=30)
    return r.returncode == 0


@pytest.mark.parametrize("provider", [AwsHostProvider, GcpHostProvider], ids=["aws", "gcp"])
@pytest.mark.parametrize(
    "state, ready",
    [("none", False), ("all-disabled", False), ("enabled", True)],
)
def test_repos_ready_only_with_an_enabled_repository(tmp_path, provider, state, ready):
    assert _ready(tmp_path, provider._REPOS_READY, state) is ready


def test_repos_ready_passes_on_images_without_zypper(tmp_path):
    env = {**os.environ, "PATH": str(tmp_path)}
    r = subprocess.run(["/bin/bash", "-c", AwsHostProvider._REPOS_READY], env=env, timeout=30)
    assert r.returncode == 0


def test_aws_provision_waits_for_the_image_repositories(managed_ssh, monkeypatch):  # noqa: F811
    """SLES PAYG on EC2 registers its repos after sshd answers; the installer needs them."""
    calls = []

    class Result:
        def __init__(self, ok):
            self.ok = ok

    def fake_run(inv, fh, cmd, **kw):
        calls.append(cmd)
        return Result(len(calls) >= 3)

    monkeypatch.setattr(aws, "run_remote", fake_run)
    monkeypatch.setattr(AwsHostProvider, "_wait_ssh", lambda *a, **k: None)
    monkeypatch.setattr(aws, "plant_rodeo_ssh_key", lambda *a, **k: None)
    p = AwsHostProvider(ec2_client=_FakeEC2(), sleep=lambda s: None)
    spec = ProvisionSpec(workshop="demo", host_ids=["primary"], ssh_user="ec2-user", wait_ssh=True)
    p.provision(spec, {
        "type": "aws", "region": "ap-northeast-1", "instance_type": "m8id.4xlarge",
        "ami": "ami-x", "subnet_id": "subnet-1", "security_group_ids": ["sg-1"],
    })
    assert len(calls) == 3 and "zypper" in calls[0][-1]
