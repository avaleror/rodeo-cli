"""Seed a ready lab directory from a bundled example, for the `up` flow.

`rodeo up` needs a lab dir that deploys with zero ceremony: file-based secrets
(``??key``, read from ~/.rodeo/secrets.yaml — no env vars, no ``sudo -E``) and no
host-specific assumptions baked into the plan. This module copies a bundled
example and normalizes its plan to those defaults. The lower-level `init` command
keeps its own env-var form for CI; this is the beginner path.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

_EXAMPLES = Path(__file__).parent / "data" / "examples"

# Beginner-facing profile name -> bundled example directory.
PROFILE_EXAMPLE = {
    "rancher": "rancher-lab-config",  # 1 VM: Rancher Prime on K3s, no Harvester (smallest)
    "test": "harvester-lab-config",   # 2-node Harvester, no Rancher (modest hosts)
    "harvester": "harvester",         # full 3-node Harvester + Rancher Prime
}


def example_dir(profile: str) -> Path:
    """Resolve a profile name to its bundled example directory."""
    name = PROFILE_EXAMPLE.get(profile, profile)
    src = _EXAMPLES / name
    if not src.is_dir():
        raise FileNotFoundError(f"No bundled example for profile '{profile}' (looked for {src})")
    return src


def seed_lab(profile: str, dest: Path, force: bool = False) -> Path:
    """Copy a profile's example into ``dest`` and normalize its plan for `up`.

    Returns the lab directory. Leaves credentials in ``??key`` form so deploy reads
    them straight from ~/.rodeo/secrets.yaml. Blanks any host-specific storage device
    so a single-disk machine works out of the box.
    """
    dest = dest.expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    src = example_dir(profile)

    for item in src.iterdir():
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                if not force:
                    continue
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            if target.exists() and not force:
                continue
            shutil.copy2(item, target)

    normalize_plan(dest / "rodeo-plan.yaml", name=dest.name)
    return dest


def normalize_plan(plan_path: Path, name: str | None = None) -> None:
    """Make a seeded plan beginner-safe: baremetal, file-secret creds, no fixed disk.

    A round-trip through YAML drops the example's comments, which is fine for a
    generated lab. Keeps ``??key`` (file) credential form, never ``??env:``.
    """
    if not plan_path.exists():
        return
    data = yaml.safe_load(plan_path.read_text()) or {}

    if name:
        data["name"] = name
    data["deployment_target"] = "baremetal"

    # Single-disk hosts: never inherit a hard-coded device like /dev/nvme1n1.
    storage = data.get("storage")
    if isinstance(storage, dict) and storage.get("device"):
        storage["device"] = ""

    # Ensure credentials resolve from ~/.rodeo/secrets.yaml (file form).
    creds = data.get("credentials")
    if isinstance(creds, dict):
        for key in list(creds.keys()):
            val = creds[key]
            if isinstance(val, str) and val.startswith("??env:"):
                creds[key] = f"??{key}"

    plan_path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))
