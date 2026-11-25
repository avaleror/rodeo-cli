"""`rodeo fleet cert`: one ``*.<zone>`` certificate per event, on the laptop.

DNS-01 against the operator's Route 53 zone with a pinned ``lego`` binary
(downloaded once to ``~/.rodeo/bin``, sha256-checked against the value
pinned here). lego gets the laptop's current AWS credentials as short-lived
environment variables resolved by boto3, so ``aws login`` sessions work the
same as for every other rodeo AWS call. Lab hosts never run ACME and never
hold AWS credentials: ``rodeo fleet player-setup`` pushes the files over SSH.

Let's Encrypt allows 50 certificates per registered domain per week; one
wildcard per event keeps a 10-lab rehearsal far from that. A re-run reuses
the certificate until it is inside lego's renewal window.
"""
from __future__ import annotations

import os
import platform
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from ..config import ConfigError
from .inventory import FleetInventory

LEGO_VERSION = "5.6.0"
LEGO_SHA256 = {
    ("darwin", "arm64"): "37453b4e1ad7e5d55a969508765db004122b9acbcfe099b95cafb86d0025a383",
    ("darwin", "amd64"): "0c2b5c5e65c20fbd28f28331de24bf315fa4a8669beb0d75fd7eae530d10e0f4",
    ("linux", "amd64"): "f02fc11b6f56b97e387fa7caa0268f86f5a336953b22458e6430e4651fea439a",
    ("linux", "arm64"): "34d87a6552d69390c4a4560237045c47713a8e148d899e16db0e97040894ed1f",
}
LE_STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"
CERT_NAME = "wildcard"
MIN_DAYS_LEFT = 7  # player-setup refuses to push a certificate closer to expiry


@dataclass(frozen=True)
class CertFiles:
    cert: Path   # leaf + chain, PEM
    key: Path    # private key, PEM, 0600
    not_after: float  # epoch seconds

    @property
    def days_left(self) -> float:
        return (self.not_after - time.time()) / 86400


def _platform() -> tuple[str, str]:
    system = platform.system().lower()
    machine = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine().lower(), platform.machine().lower())
    return system, machine


def cert_dir(domain: str) -> Path:
    return Path.home() / ".rodeo" / "certs" / domain


def lego_path() -> Path:
    return Path.home() / ".rodeo" / "bin" / f"lego-{LEGO_VERSION}"


def ensure_lego() -> Path:
    """The pinned lego binary, downloaded and checked once."""
    path = lego_path()
    if path.is_file() and os.access(path, os.X_OK):
        return path
    plat = _platform()
    want = LEGO_SHA256.get(plat)
    if want is None:
        raise ConfigError(f"no pinned lego build for {plat[0]}/{plat[1]}")
    url = (f"https://github.com/go-acme/lego/releases/download/v{LEGO_VERSION}/"
           f"lego_v{LEGO_VERSION}_{plat[0]}_{plat[1]}.tar.gz")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory() as tmp:
        tgz = Path(tmp) / "lego.tgz"
        with urllib.request.urlopen(url, timeout=120) as resp:  # pinned https URL, checked below
            tgz.write_bytes(resp.read())
        got = sha256(tgz.read_bytes()).hexdigest()
        if got != want:
            raise ConfigError(f"lego download checksum mismatch ({got}), refusing to run it")
        with tarfile.open(tgz) as tar:
            member = tar.getmember("lego")
            src = tar.extractfile(member) if member.isfile() else None
            if src is None:
                raise ConfigError("lego archive has no plain 'lego' file")
            tmp_bin = Path(tmp) / "lego"
            tmp_bin.write_bytes(src.read())
        shutil.move(str(tmp_bin), path)
    path.chmod(0o700)
    return path


def aws_env() -> dict[str, str]:
    """The laptop's AWS credentials as env vars for lego (resolved by boto3)."""
    from ..providers.aws import _require_boto3

    _require_boto3()
    import boto3

    creds = boto3.Session().get_credentials()
    if creds is None:
        raise ConfigError("no AWS credentials on this machine (run `aws login`)")
    frozen = creds.get_frozen_credentials()
    env = {"AWS_ACCESS_KEY_ID": frozen.access_key, "AWS_SECRET_ACCESS_KEY": frozen.secret_key,
           "AWS_REGION": "us-east-1"}
    if frozen.token:
        env["AWS_SESSION_TOKEN"] = frozen.token
    return env


def lego_argv(lego: Path, inventory: FleetInventory) -> list[str]:
    p = inventory.player
    assert p is not None
    argv = [str(lego), "run", "--accept-tos", "--path", str(cert_dir(p.domain)),
            "--dns", "route53", "--domains", f"*.{p.domain}", "--cert.name", CERT_NAME]
    if p.acme_email:
        argv += ["--email", p.acme_email]
    if p.staging:
        argv += ["--server", LE_STAGING]
    return argv


def _not_after(cert: Path) -> float:
    decoded = ssl._ssl._test_decode_cert(str(cert))  # type: ignore[attr-defined]
    return ssl.cert_time_to_seconds(decoded["notAfter"])


def current(domain: str) -> CertFiles | None:
    """The certificate lego left for ``domain``, if any."""
    base = cert_dir(domain) / "certificates"
    cert, key = base / f"{CERT_NAME}.crt", base / f"{CERT_NAME}.key"
    if not (cert.is_file() and key.is_file()):
        return None
    return CertFiles(cert, key, _not_after(cert))


def issue(inventory: FleetInventory, *, zone_id: str | None = None) -> CertFiles:
    """Obtain or renew ``*.<domain>``. Needs Route 53 rights on that zone only."""
    if inventory.player is None:
        raise ConfigError("this workshop has no player: block (player.domain: <your zone>)")
    domain = inventory.player.domain
    lego = ensure_lego()
    cert_dir(domain).mkdir(parents=True, exist_ok=True, mode=0o700)
    env = {**os.environ, **aws_env()}
    for k in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        env.pop(k, None)  # the frozen keys above are the ones to use
    if zone_id:
        env["AWS_HOSTED_ZONE_ID"] = zone_id
    res = subprocess.run(lego_argv(lego, inventory), env=env, text=True, capture_output=True,
                         timeout=900, check=False)
    if res.returncode != 0:
        tail = "\n".join((res.stderr or res.stdout).strip().splitlines()[-8:])
        raise ConfigError(f"lego failed (exit {res.returncode}):\n{tail}")
    files = current(domain)
    if files is None:
        raise ConfigError(f"lego finished but left no certificate in {cert_dir(domain)}")
    files.key.chmod(0o600)
    return files


def bundle_for_push(domain: str) -> str:
    """Cert chain then key, PEM, for ``rodeo player setup --tls-stdin``."""
    files = current(domain)
    if files is None:
        raise ConfigError(f"no certificate for *.{domain} yet: run `rodeo fleet cert` first")
    if files.days_left < MIN_DAYS_LEFT:
        raise ConfigError(f"*.{domain} expires in {files.days_left:.0f} days: run `rodeo fleet cert` again")
    return files.cert.read_text().rstrip() + "\n" + files.key.read_text().rstrip() + "\n"
