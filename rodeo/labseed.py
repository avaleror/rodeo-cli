"""Seed a ready lab directory from a bundled example, for the `up` flow.

`rodeo up` needs a lab dir that deploys with zero ceremony: file-based secrets
(``??key``, read from ~/.rodeo/secrets.yaml — no env vars, no ``sudo -E``) and no
host-specific assumptions baked into the plan. This module copies a bundled
example and normalizes its plan to those defaults. The lower-level `init` command
keeps its own env-var form for CI; this is the beginner path.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml

_EXAMPLES = Path(__file__).parent / "data" / "examples"

# Beginner-facing profile name -> bundled example directory.
PROFILE_EXAMPLE = {
    "rancher": "rancher-lab-config",        # 1 VM: Rancher Prime on K3s, no Harvester (smallest)
    "test": "harvester-lab-config",         # 2-node Harvester, no Rancher (modest hosts)
    "harvester-ha": "harvester-ha-config",  # 3-node Harvester, no Rancher (3-member etcd HA, lean sizing)
    "harvester-2n": "harvester-2n",         # 2-node Harvester + Rancher Prime (slim profile, ~56 GiB RAM)
    "harvester": "harvester",               # full 3-node Harvester + Rancher Prime
    "suse-edge": "suse-edge",               # Rancher + Elemental + EIB + 4 edge nodes (SUSE Edge 3.6)
}

_LAB_MARKERS = ("rodeo-plan.yaml", "definition.yaml")


def custom_profiles_root() -> Path:
    """Where user-created profiles live (resolved live so HOME/sudo are honored)."""
    return Path.home() / ".rodeo" / "profiles"


def custom_profile_dir(name: str) -> Path:
    return custom_profiles_root() / name


def _is_lab_dir(path: Path) -> bool:
    return path.is_dir() and any((path / m).exists() for m in _LAB_MARKERS)


def example_dir(profile: str) -> Path:
    """Resolve a bundled profile name to its example directory."""
    name = PROFILE_EXAMPLE.get(profile, profile)
    src = _EXAMPLES / name
    if not src.is_dir():
        raise FileNotFoundError(f"No bundled example for profile '{profile}' (looked for {src})")
    return src


def profile_kind(name: str) -> str | None:
    """Return 'bundled', 'custom', or None for a profile name."""
    if name in PROFILE_EXAMPLE:
        return "bundled"
    if _is_lab_dir(custom_profile_dir(name)):
        return "custom"
    return None


def resolve_profile_source(name: str) -> Path:
    """Source config-dir for a profile name: bundled example or custom profile dir."""
    kind = profile_kind(name)
    if kind == "bundled":
        return example_dir(name)
    if kind == "custom":
        return custom_profile_dir(name)
    raise FileNotFoundError(
        f"No profile named '{name}'. Bundled: {', '.join(PROFILE_EXAMPLE)}. "
        f"Create a custom one with:  rodeo new {name}"
    )


def list_profiles() -> list[dict]:
    """All available profiles: bundled examples plus the user's custom ones."""
    out = [
        {"name": n, "kind": "bundled", "path": str(_EXAMPLES / PROFILE_EXAMPLE[n])}
        for n in PROFILE_EXAMPLE
    ]
    root = custom_profiles_root()
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if _is_lab_dir(d):
                out.append({"name": d.name, "kind": "custom", "path": str(d)})
    return out


def scaffold_profile(name: str, from_base: str = "harvester", force: bool = False) -> Path:
    """Create an editable custom profile under ~/.rodeo/profiles/<name> from a base.

    Copies a working bundled lab (so the topology deploys as-is), retitles it, and
    keeps all the definition.yaml comments intact for editing. Run it later with
    ``rodeo up --profile <name>``.
    """
    if from_base not in PROFILE_EXAMPLE:
        raise FileNotFoundError(
            f"--from '{from_base}' is not a bundled base. Use one of: {', '.join(PROFILE_EXAMPLE)}"
        )
    dest = custom_profile_dir(name)
    if dest.exists() and not force:
        raise FileExistsError(f"Profile '{name}' already exists at {dest} (use --force to overwrite)")
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(example_dir(from_base), dest)

    normalize_plan(dest / "rodeo-plan.yaml", name=name)
    _retitle_definition(dest / "definition.yaml", name)
    return dest


def _retitle_definition(def_path: Path, name: str) -> None:
    """Set the definition name and prepend an edit-me header, preserving comments."""
    if not def_path.exists():
        return
    text = def_path.read_text()
    # The definition name is the only 2-space-indented `name:` (node names are deeper).
    text = re.sub(r"(?m)^(  name:\s*).*$", rf"\g<1>{name}", text, count=1)
    header = (
        f"# Custom rodeo '{name}' — scaffolded by 'rodeo new'. Edit freely.\n"
        f"# Deploy it with:  rodeo up --profile {name}\n"
        f"# Definition format + how-to:  docs/custom-rodeos.md\n\n"
    )
    def_path.write_text(header + text)


def seed_lab(
    profile: str,
    dest: Path,
    force: bool = False,
    deployment_target: str = "baremetal",
) -> Path:
    """Copy a profile's source into ``dest`` and normalize its plan for `up`.

    Works for bundled examples and custom profiles. Returns the lab directory.
    Leaves credentials in ``??key`` form so deploy reads them straight from
    ~/.rodeo/secrets.yaml. Blanks any host-specific storage device so a single-disk
    machine works out of the box.
    """
    dest = dest.expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    src = resolve_profile_source(profile)

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

    normalize_plan(dest / "rodeo-plan.yaml", name=dest.name, deployment_target=deployment_target)
    return dest


def normalize_plan(
    plan_path: Path,
    name: str | None = None,
    deployment_target: str = "baremetal",
) -> None:
    """Make a seeded plan beginner-safe: file-secret creds, no fixed disk.

    A round-trip through YAML drops the example's comments, which is fine for a
    generated lab. Keeps ``??key`` (file) credential form, never ``??env:``.
    """
    if not plan_path.exists():
        return
    data = yaml.safe_load(plan_path.read_text()) or {}

    if name:
        data["name"] = name
    if deployment_target not in ("baremetal", "instruqt"):
        raise ValueError(f"Unknown deployment_target: {deployment_target!r}")
    data["deployment_target"] = deployment_target

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
