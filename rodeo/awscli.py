"""AWS CLI and credentials for plans that deploy into AWS.

Two plans deploy into AWS:

- ``type: lab-in-a-box`` with ``lab_in_a_box.cloud.cloudtype: aws``: lab-in-a-box
  creates the VMs with the ``aws`` CLI, using the plan's settings (``AWS_PROFILE``,
  or ``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` / ``AWS_SESSION_TOKEN``,
  and ``AWS_REGION``).
- ``deployment_target: aws`` from a workstation: rodeo provisions the EC2 KVM
  host itself with boto3 and the default AWS credential chain.

:func:`install_cli` installs the official AWS CLI v2 (``/usr/local/aws-cli``,
``/usr/local/bin/aws``) after checking the installer's PGP signature against
AWS's published key (``data/aws-cli-public-key.asc``, fingerprint
:data:`KEY_FINGERPRINT`). :func:`check_credentials` asks AWS who the plan's
credentials belong to, so a deploy stops before it starts when they are
missing or expired.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

INSTALLER_URL = "https://awscli.amazonaws.com/awscli-exe-linux-{arch}.zip"
KEY_FILE = Path(__file__).parent / "data" / "aws-cli-public-key.asc"
KEY_FINGERPRINT = "FB5DB77FD5C118B80511ADA8A6310ACC4672475C"
LOCAL_BIN = Path("/usr/local/bin")
_ARCHES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
_CRED_VARS = ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
              "AWS_REGION", "AWS_DEFAULT_REGION")

Runner = Callable[..., subprocess.CompletedProcess]


def labinabox_aws(cfg: dict) -> dict | None:
    """The lab-in-a-box cloud spec when its VMs are created in AWS, else None."""
    if cfg.get("type") != "lab-in-a-box":
        return None
    from .labinabox_host import cloud

    spec = cloud(cfg)
    return spec if spec and spec.get("cloudtype") == "aws" else None


def provider_aws(cfg: dict) -> dict | None:
    """The plan's ``provider`` block when rodeo provisions an EC2 host itself, else None."""
    provider = cfg.get("provider")
    if cfg.get("deployment_target") == "aws" and isinstance(provider, dict) \
            and str(provider.get("type") or "aws").lower() == "aws":
        return provider
    return None


def deploys_to_aws(cfg: dict) -> bool:
    return labinabox_aws(cfg) is not None or provider_aws(cfg) is not None


def ensure_on_path() -> str | None:
    """Path of the ``aws`` CLI, adding /usr/local/bin to PATH when it is only there
    (sudo's secure_path drops it on SLES), so lab-in-a-box finds it too."""
    found = shutil.which("aws")
    if found:
        return found
    local = LOCAL_BIN / "aws"
    if local.is_file() and os.access(local, os.X_OK):
        os.environ["PATH"] = str(LOCAL_BIN) + os.pathsep + os.environ.get("PATH", "")
        return str(local)
    return None


def cli_env(settings: dict[str, Any], base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for the ``aws`` CLI with exactly the plan's credentials: a profile
    wins over keys (as in lab-in-a-box), and the caller's own AWS_* variables are dropped."""
    env = {k: v for k, v in (base if base is not None else os.environ).items() if k not in _CRED_VARS}
    region = str(settings.get("AWS_REGION") or "")
    if region:
        env["AWS_REGION"] = env["AWS_DEFAULT_REGION"] = region
    if settings.get("AWS_PROFILE"):
        env["AWS_PROFILE"] = str(settings["AWS_PROFILE"])
        return env
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        if settings.get(key):
            env[key] = str(settings[key])
    return env


def _hint(settings: dict[str, Any]) -> str:
    if settings.get("AWS_PROFILE"):
        profile = settings["AWS_PROFILE"]
        return (f"log in to profile '{profile}' as the user that runs rodeo "
                f"(aws sso login --profile {profile}, or aws configure --profile {profile})")
    return ("set aws_access_key_id and aws_secret_access_key (plus aws_session_token for "
            "temporary keys) in ~/.rodeo/secrets.yaml")


def check_credentials(cfg: dict, run: Runner = subprocess.run) -> tuple[bool, str]:
    """(ok, detail) for the AWS credentials this plan deploys with. Detail is the
    caller's ARN when they work, else what failed and how to fix it."""
    spec = labinabox_aws(cfg)
    if spec is not None:
        settings = spec.get("settings") or {}
        if not settings.get("AWS_PROFILE") and not (settings.get("AWS_ACCESS_KEY_ID")
                                                    and settings.get("AWS_SECRET_ACCESS_KEY")):
            return False, "no AWS credentials in lab_in_a_box.cloud.settings: " + _hint(settings)
        aws = ensure_on_path()
        if aws is None:
            return False, "the aws CLI is not installed: sudo rodeo install-deps --aws"
        try:
            r = run([aws, "sts", "get-caller-identity", "--query", "Arn", "--output", "text"],
                    capture_output=True, text=True, timeout=60, env=cli_env(settings))
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"aws sts get-caller-identity: {exc}"
        if r.returncode == 0:
            return True, r.stdout.strip()
        return False, f"{(r.stderr or r.stdout).strip()[-300:]} — {_hint(settings)}"
    provider = provider_aws(cfg)
    if provider is not None:
        return boto3_identity(str(provider.get("region") or ""))
    return True, "not deploying into AWS"


def boto3_identity(region: str) -> tuple[bool, str]:
    """(ok, ARN or error) for the default AWS credential chain, via boto3."""
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        return False, "boto3 is not installed (the [aws] extra)"
    try:
        sts = boto3.client("sts", region_name=region or None)
        return True, str(sts.get_caller_identity()["Arn"])
    except (BotoCoreError, ClientError) as exc:
        return False, (f"{exc} — set AWS credentials for boto3 (AWS_PROFILE after aws sso login, "
                       "or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY)")


def installer_url(machine: str | None = None) -> str:
    arch = _ARCHES.get((machine or platform.machine()).lower())
    if arch is None:
        raise RuntimeError(f"no AWS CLI v2 installer for {machine or platform.machine()}")
    return INSTALLER_URL.format(arch=arch)


def verified(status_output: str) -> bool:
    """True when gpg's --status-fd output has a valid signature by AWS's CLI key.

    VALIDSIG ends with the primary key fingerprint; an expired key still yields
    VALIDSIG (with EXPKEYSIG), a bad or foreign signature never does.
    """
    for line in status_output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[:2] == ["[GNUPG:]", "VALIDSIG"] and parts[-1] == KEY_FINGERPRINT:
            return True
    return False


def install_cli(run: Runner = subprocess.run, machine: str | None = None) -> str:
    """Install or update the AWS CLI v2 from AWS, signature-checked. Returns `aws --version`.

    Needs root, curl, gpg and unzip. Raises RuntimeError on any failure; nothing is
    installed unless the signature checks out.
    """
    for tool in ("curl", "gpg", "unzip"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"{tool} is needed to install the AWS CLI")
    url = installer_url(machine)
    with tempfile.TemporaryDirectory(prefix="rodeo-awscli-") as tmp:
        work = Path(tmp)
        zip_path, sig_path, home = work / "awscliv2.zip", work / "awscliv2.zip.sig", work / "gnupg"
        home.mkdir(mode=0o700)
        steps = [
            ["curl", "-4", "-fsSL", "--retry", "3", "-o", str(zip_path), url],
            ["curl", "-4", "-fsSL", "--retry", "3", "-o", str(sig_path), url + ".sig"],
            ["gpg", "--batch", "--homedir", str(home), "--import", str(KEY_FILE)],
        ]
        for cmd in steps:
            r = run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"{cmd[0]} {cmd[-1]}: {(r.stderr or '').strip()[-300:]}")
        r = run(["gpg", "--batch", "--homedir", str(home), "--status-fd", "1", "--verify",
                 str(sig_path), str(zip_path)], capture_output=True, text=True)
        if not verified(r.stdout or ""):
            raise RuntimeError("the AWS CLI installer's signature does not match AWS's key "
                               f"{KEY_FINGERPRINT}; nothing installed")
        for cmd in (["unzip", "-q", "-o", str(zip_path), "-d", str(work)],
                    [str(work / "aws" / "install"), "--update"]):
            r = run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"{Path(cmd[0]).name}: {(r.stderr or '').strip()[-300:]}")
    aws = ensure_on_path()
    if aws is None:
        raise RuntimeError("the AWS CLI installer finished but aws is not in /usr/local/bin")
    r = run([aws, "--version"], capture_output=True, text=True)
    return (r.stdout or r.stderr or "").strip()
