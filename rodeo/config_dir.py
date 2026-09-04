"""Support for --config-dir (EIB-inspired configuration directory).

A config dir is a self-contained folder with:
  - (optional) definition.yaml  — custom/override for the rodeo type
  - (optional) rodeo-plan.yaml  — the user's plan + parameters
  - certs/      — CA certs etc to make available
  - manifests/  — k8s manifests (for pre-apply or image extraction like EIB)
  - helm/       — values/ for charts, etc.
  - custom/scripts/ — ordered (numbered) scripts for custom steps
  - rpms/, network/, files/, pxe/ etc. as needed later

The dir is recorded in inventory under _config_dir so renderer/build phases
can consume artifacts (e.g. embed manifests, copy scripts).
For Phase 2: support loading definition from the dir + recording contents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_config_dir(path: str | Path | None = None) -> dict[str, Any]:
    """Return info dict for a config dir, or {} if none.

    Includes 'root', and lists/paths for known subdirs.
    Safe if dir doesn't exist.
    """
    if not path:
        return {}

    root = Path(path).resolve()
    info: dict[str, Any] = {
        "root": str(root),
        "exists": root.is_dir(),
    }

    if not root.is_dir():
        return info

    # definition (if present, already preferred by inventory load)
    defn = root / "definition.yaml"
    if defn.exists():
        info["definition_path"] = str(defn)

    # plan (if present, may have been used by load_config)
    planf = root / "rodeo-plan.yaml"
    if planf.exists():
        info["plan_path"] = str(planf)

    # certs/
    certs = root / "certs"
    if certs.is_dir():
        info["certs"] = sorted(str(f) for f in certs.iterdir() if f.is_file())

    # manifests/ (for pre-seeding clusters, like EIB embedded registry extraction)
    manifests = root / "manifests"
    if manifests.is_dir():
        info["manifests"] = sorted(str(f) for f in manifests.iterdir() if f.is_file())

    # helm/ (values etc)
    helm = root / "helm"
    if helm.is_dir():
        values_dir = helm / "values"
        info["helm"] = {
            "values": sorted(str(f) for f in values_dir.glob("*.yaml")) if values_dir.is_dir() else [],
        }

    # custom/scripts/ — prefer numbered for deterministic order (like combustion)
    scripts_dir = root / "custom" / "scripts"
    if scripts_dir.is_dir():
        scripts: list[str] = []
        for f in sorted(scripts_dir.iterdir()):
            if f.is_file():
                scripts.append(str(f))
        info["custom_scripts"] = scripts

    # story/ — rmstory-tagged workshop narrative (see rodeo/story.py):
    #   story/*.md      tagged markdown sources
    #   story/stories/  rmstory story-variant indexes (<id>.yaml)
    #   story/strings/  rmstory translation store (filesystem backend)
    story = root / "story"
    if story.is_dir():
        info["story"] = {
            "root": str(story),
            "sources": sorted(str(f) for f in story.glob("*.md")),
        }

    # Future extensions (rpms, network per-host, os-files, pxe-extras, etc.)
    # can be added here without breaking callers.

    return info
