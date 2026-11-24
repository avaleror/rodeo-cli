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
# AWS is *where* (--target aws / deployment_target: aws), not a second topology.
PROFILE_EXAMPLE = {
    "rancher-test": "rancher-test",         # Rancher Prime + single-node K3s and RKE2 clusters (smallest)
    "rancher": "rancher-lab-config",        # Rancher Prime + K3s, RKE2 and 3-node RKE2 clusters
    "test": "harvester-lab-config",         # 2-node Harvester, no Rancher (modest hosts)
    "harvester-ha": "harvester-ha-config",  # 3-node Harvester, no Rancher (3-member etcd HA, lean sizing)
    "harvester-2n": "harvester-2n",         # 2-node Harvester + Rancher Prime (slim profile, ~56 GiB RAM)
    "harvester": "harvester",               # full 3-node Harvester + Rancher Prime
    "virt-workshop": "virt-workshop",       # harvester topology + custom/scripts/ (image cache, NFS, pre-created VM) for suse-virt-workshop
    "virt-workshop-2n": "virt-workshop-2n", # budget tier: 2-node Harvester (like harvester-2n) + same custom/scripts/, sized for m8id.4xlarge
    "suse-edge": "suse-edge",               # Rancher + Elemental + EIB + 4 edge nodes (SUSE Edge 3.6)
    "suse-telco": "suse-telco",             # Telco Cloud 3.7: mgmt + site-co + site-ran (topology only)
    "smlm-workshop": "smlm-workshop",       # lab-in-a-box: SUSE Multi-Linux Manager + 8 clients (instruqt-SMLM track)
    "kvm-hosts": "kvm-hosts",               # plain KVM hosts, no VMs (fleet: a small KVM cluster, dead-man switch optional)
}

# Old profile names that still work, mapped to their current name. The
# "-aws" suffix suggested the profile only ran on AWS; where a lab runs is
# --target, not the profile (renamed 2026-11-13).
PROFILE_ALIASES = {
    "virt-workshop-aws": "virt-workshop",
    "virt-workshop-aws-2n": "virt-workshop-2n",
}

_LAB_MARKERS = ("rodeo-plan.yaml", "definition.yaml")


def canonical_profile(name: str | None, *, warn: bool = True) -> str | None:
    """Map a deprecated profile name to its current one (warning once on stderr)."""
    if not name:
        return name
    new = PROFILE_ALIASES.get(name.strip())
    if new is None:
        return name
    if warn and name not in _warned:
        _warned.add(name)
        import sys

        print(
            f"⚠  Profile '{name}' was renamed to '{new}'. The old name still works "
            f"for now; please switch to '{new}'.",
            file=sys.stderr,
        )
    return new


_warned: set[str] = set()


def custom_profiles_root() -> Path:
    """Where user-created profiles live (resolved live so HOME/sudo are honored)."""
    from .paths import rodeo_profiles_dir

    return rodeo_profiles_dir()


def custom_profile_dir(name: str) -> Path:
    return custom_profiles_root() / name


def _is_lab_dir(path: Path) -> bool:
    return path.is_dir() and any((path / m).exists() for m in _LAB_MARKERS)


def example_dir(profile: str) -> Path:
    """Resolve a bundled profile name to its example directory."""
    profile = canonical_profile(profile)
    name = PROFILE_EXAMPLE.get(profile, profile)
    src = _EXAMPLES / name
    if not src.is_dir():
        raise FileNotFoundError(f"No bundled example for profile '{profile}' (looked for {src})")
    return src


def profile_kind(name: str) -> str | None:
    """Return 'bundled', 'custom', or None for a profile name."""
    if canonical_profile(name) in PROFILE_EXAMPLE:
        return "bundled"
    if _is_lab_dir(custom_profile_dir(name)):
        return "custom"
    return None


def resolve_profile_source(name: str) -> Path:
    """Source config-dir for a profile name: bundled example or custom profile dir."""
    name = canonical_profile(name)
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
    from_base = canonical_profile(from_base)
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
    # Auto-trust only when rodeo writes every file trust covers in this call
    # (an empty target) from a bundled profile or a custom one already trusted.
    # A clone or a hand-made dir passed as the target needs `rodeo trust`.
    from .config import has_hashed_files, is_lab_trusted, trust_lab

    fresh = not has_hashed_files(dest)
    source_trusted = profile_kind(profile) == "bundled" or is_lab_trusted(src)

    from .config import ConfigError
    from .labfs import write_lab_file

    copied: list[Path] = []
    for item in src.iterdir():
        target = dest / item.name
        # Checked before exists(): a dangling link (rodeo-plan.yaml -> ~/.bash_aliases)
        # reports False there and would be created through.
        if target.is_symlink():
            raise ConfigError(f"{target} is a symlink: rodeo won't seed a lab through it")
        if item.is_dir():
            if target.exists():
                if not force:
                    continue
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            if target.exists() and not force:
                continue
            write_lab_file(dest, item.name, item.read_bytes(), item.stat().st_mode & 0o777)
            shutil.copystat(item, target)
        copied.append(item)

    _verify_seed_copy(src, dest, copied)

    normalize_plan(dest / "rodeo-plan.yaml", name=dest.name, deployment_target=deployment_target)
    if fresh and source_trusted:
        trust_lab(dest)
    return dest


def fill_missing_from_profile(profile: str, dest: Path) -> list[str]:
    """Copy the top-level items of a bundled *profile* that *dest* lacks.

    For the AWS host: the laptop ships only the plan files (rodeo-plan.yaml,
    definition.yaml), so the host finds an existing lab and never seeds it.
    The profile's custom/scripts, checks/, manifests/ and the rest were then
    missing, and custom_scripts ran nothing (virt-workshop came up without
    webserver-prod, the image cache, the NFS export or the student kit).

    Never overwrites and never touches the plan files. Bundled profiles only:
    their source is trusted, so callers keep the lab's trust with keep_trust.
    Returns the names it added.
    """
    from .labfs import write_lab_file

    if profile_kind(profile) != "bundled":
        return []
    src = resolve_profile_source(profile)
    dest = dest.expanduser().resolve()
    added: list[str] = []
    for item in sorted(src.iterdir()):
        if item.name in ("rodeo-plan.yaml", "definition.yaml") or item.name.startswith("."):
            continue
        target = dest / item.name
        if target.is_symlink() or target.exists():
            continue
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            write_lab_file(dest, item.name, item.read_bytes(), item.stat().st_mode & 0o777)
        added.append(item.name)
    return added


def _verify_seed_copy(src: Path, dest: Path, copied: list[Path]) -> None:
    """Confirm every file actually copied this call landed in ``dest`` intact
    — defense in depth, not a fix for a root cause we could pin down.

    ``copied`` is the subset of ``src``'s top-level items this call actually
    wrote (excludes anything left alone because it already existed at
    ``dest`` and ``force`` was not set — e.g. a student's own hand-edits on a
    re-run must not be flagged as "corrupt" just for differing from the
    pristine source).

    Found live 2026-09-18: seeding ``virt-workshop-aws`` on a fresh AWS host
    somehow produced a ``custom/scripts/`` with a lone, unexplained
    ``.gitkeep`` (that filename does not exist anywhere in this profile's
    git history) instead of its 3 real scripts — even though the exact same
    ``seed_lab()`` call reproduced correctly moments later on the identical
    host. Root cause not confirmed (candidates: a first-write consistency
    hiccup on a freshly-restored EBS root volume; something environment-
    specific we didn't reproduce). Whatever it was, the failure mode is what
    matters: ``rodeo up`` reported success on a half-seeded lab, and
    ``stream_custom_scripts()`` then silently no-ops on a ``custom/scripts/``
    that exists but holds no executable files (see ``runner.py`` — that is
    by design, for profiles that legitimately ship none). A partial seed
    must fail loudly right here instead of surfacing as a mystery hours
    later. Only checks regular files (skips symlinks) — none of today's
    bundled examples use them, and comparing size is more useful than a full
    hash while remaining cheap for hundreds of small config files.
    """
    mismatches = []
    for item in copied:
        paths = item.rglob("*") if item.is_dir() else [item]
        for path in paths:
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(src)
            target = dest / rel
            if not target.is_file():
                mismatches.append(f"{rel} (missing)")
            elif target.stat().st_size != path.stat().st_size:
                mismatches.append(f"{rel} (size mismatch)")
    if mismatches:
        raise RuntimeError(
            f"seed_lab: incomplete copy from {src} to {dest} — "
            f"{', '.join(sorted(mismatches))}. This should not happen; "
            "delete the lab dir and retry (rodeo up --dir ... again)."
        )


def normalize_plan(
    plan_path: Path,
    name: str | None = None,
    deployment_target: str = "baremetal",
) -> None:
    """Make a seeded plan beginner-safe: file-secret creds, no fixed disk.

    A round-trip through YAML drops the example's comments, which is fine for a
    generated lab. Keeps ``??key`` (file) credential form, never ``??env:``.
    When ``deployment_target`` is ``instruqt``, also apply host-aware resource
    presets so new labs do not oversubscribe nested KVM by default.
    """
    if not plan_path.exists():
        return
    data = yaml.safe_load(plan_path.read_text()) or {}

    if name:
        data["name"] = name
    if deployment_target not in ("baremetal", "instruqt", "aws", "gcp", "remote"):
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

    if deployment_target == "instruqt":
        import os

        from .sizing import apply_instruqt_resource_presets, flavor_counts_from_definition

        counts = flavor_counts_from_definition(plan_path.parent / "definition.yaml")
        apply_instruqt_resource_presets(
            data,
            host_cpus=os.cpu_count() or 0,
            flavor_counts=counts,
        )
    elif deployment_target in ("aws", "gcp"):
        from .host_context import apply_host_context

        adapted, _ = apply_host_context(data, host_facts={})
        data.clear()
        data.update(adapted)

    from .labfs import write_lab_file

    write_lab_file(plan_path.parent, plan_path.name,
                   yaml.safe_dump(data, sort_keys=False, default_flow_style=False), 0o644)
