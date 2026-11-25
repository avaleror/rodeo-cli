"""`rodeo fleet lab-images`: hand each lab short-lived links to private images.

The virt-rodeo profile's ``55-sles-images.sh`` downloads the SLES cloud
images the suse-virt-rodeo track expects (they need a SUSE Customer Center
login, so they live in a private bucket). The operator's laptop, which
already holds the AWS session, signs one download link per image and writes
them to ``/etc/rodeo/lab-images.list`` (root 0600) on every lab host over
SSH stdin. Lab hosts never get AWS credentials or an instance role.

Run it before ``fleet deploy`` (the custom scripts phase picks the list up),
or after, with ``--run`` to fetch the images right away. A signed link lives
as long as the shorter of ``--expires`` and the laptop's AWS session.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from ..config import ConfigError
from .fanout import fanout
from .inventory import FleetHost, FleetInventory
from .ssh_exec import run_remote

LIST_PATH = "/etc/rodeo/lab-images.list"
SCRIPT_REL = "custom/scripts/55-sles-images.sh"

# (file name, also register as a Harvester image in official-images)
VIRT_RODEO_IMAGES: tuple[tuple[str, bool], ...] = (
    ("SLES15-SP7-Minimal-VM.x86_64-Cloud-GM.qcow2", False),
    ("SLES-16.0-Minimal-VM.x86_64-Cloud-GM.qcow2", True),
)

_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_PREFIX_RE = re.compile(r"^[A-Za-z0-9._/-]*$")

Signer = Callable[[str, str, int], str]  # (bucket, key, seconds) -> https URL


@dataclass(frozen=True)
class LabImagesRow:
    id: str
    ok: bool
    detail: str = ""


def s3_signer(region: str) -> Signer:
    import boto3  # the [aws] extra; the laptop already uses it for provisioning

    client = boto3.client("s3", region_name=region)

    def sign(bucket: str, key: str, seconds: int) -> str:
        return client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key},
                                             ExpiresIn=seconds)
    return sign


def list_text(bucket: str, prefix: str, seconds: int, sign: Signer,
              images: tuple[tuple[str, bool], ...] = VIRT_RODEO_IMAGES) -> str:
    """The list file: ``<file> <signed url> [harvester]`` per image. A secret."""
    if not _BUCKET_RE.match(bucket):
        raise ConfigError(f"not an S3 bucket name: {bucket!r}")
    if not _PREFIX_RE.match(prefix) or ".." in prefix:
        raise ConfigError(f"bad key prefix: {prefix!r}")
    if not 300 <= seconds <= 7 * 24 * 3600:
        raise ConfigError("--expires must be between 300 seconds and 7 days")
    lines = []
    for name, harvester in images:
        url = sign(bucket, f"{prefix}{name}", seconds)
        if not url.startswith("https://") or any(c.isspace() for c in url):
            raise ConfigError(f"signer returned an unusable URL for {name}")
        lines.append(f"{name} {url}{' harvester' if harvester else ''}")
    return "\n".join(lines) + "\n"


def _push(inventory: FleetInventory, host: FleetHost, text: str, run: bool) -> LabImagesRow:
    write = f"install -d -m 700 /etc/rodeo && umask 077 && cat > {LIST_PATH}"
    res = run_remote(inventory, host, ["bash", "-c", write], stdin=text, timeout=60.0)
    if not res.ok:
        return LabImagesRow(host.id, False, f"could not write the list (exit {res.rc})")
    if not run:
        return LabImagesRow(host.id, True, "list written; the deploy's custom scripts fetch the images")
    script = f"{inventory.lab_dir.rstrip('/')}/{SCRIPT_REL}"
    res = run_remote(inventory, host, ["bash", script], timeout=2400.0)
    tail = (res.stdout or "").strip().splitlines()[-1:] or [f"exit {res.rc}"]
    return LabImagesRow(host.id, res.ok, tail[0][-200:])


def fleet_lab_images(inventory: FleetInventory, *, bucket: str, prefix: str = "images/",
                     seconds: int = 6 * 3600, run: bool = False, concurrency: int = 8,
                     sign: Signer | None = None) -> list[LabImagesRow]:
    if not inventory.hosts:
        raise ConfigError("no hosts in the inventory (run `rodeo fleet provision` first)")
    region = str((inventory.provider or {}).get("region") or "")
    signer = sign or s3_signer(region)
    text = list_text(bucket, prefix, seconds, signer)
    return fanout(inventory.hosts, lambda h: _push(inventory, h, text, run), concurrency=concurrency)
