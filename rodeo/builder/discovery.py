"""Data the Rodeo Builder shows, read from the rodeo-cli tree.

- engines: the four lab engines, their base profile, resources and capabilities
- workshops: bundled examples with a ``story/`` directory, and their chapters
- labinabox: where the lab-in-a-box lab-builder lives, and its add-ons

Chapter metadata (minutes, needed capabilities, check script) comes from an
optional ``story/chapters.yaml`` beside the chapters; rmstory ignores it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

DATA = Path(__file__).resolve().parent.parent / "data"
EXAMPLES = DATA / "examples"
PLATFORMS = DATA / "platforms"

LAB_BUILDER_URL = "https://rmahique.github.io/lab-in-a-box/lab-builder/"
DEFAULT_MINUTES = 10

# Capabilities each native engine gives the chapters that run on it.
ENGINES: dict[str, dict[str, Any]] = {
    "suse-virt": {
        "title": "SUSE Virtualization",
        "base": "harvester",
        "provides": ["harvester", "kubevirt", "longhorn", "rancher", "fleet"],
        "note": "3-node Harvester HCI cluster with Rancher Prime on nested KVM.",
    },
    "rancher": {
        "title": "Rancher Prime",
        "base": "rancher",
        "provides": ["rancher", "fleet"],
        "note": "Rancher Prime on K3s in a single VM, the smallest lab.",
    },
    "suse-edge": {
        "title": "SUSE Edge",
        "base": "suse-edge",
        "provides": ["rancher", "fleet", "elemental", "eib"],
        "note": "Rancher Prime, Elemental and EIB with four edge nodes.",
    },
    "lab-in-a-box": {
        "title": "lab-in-a-box",
        "base": None,
        "provides": [],
        "note": "Any lab lab-in-a-box can build: VMs, clusters and add-ons.",
        "external": True,
    },
}

# Capabilities a new chapter can ask for (the union the UI offers).
CAPABILITIES = ["rancher", "fleet", "harvester", "kubevirt", "longhorn", "neuvector",
                "harbor", "keycloak", "argocd", "smlm", "elemental", "eib"]

_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)
_TAG_RE = re.compile(r"<[^>]+>")
_SPAN_OPEN_RE = re.compile(r"<span\b[^>]*?(?<!/)>")
_COMMENT_RE = re.compile(r"<!--.*?(?:-->|$)", re.S)
_NUMBER_PREFIX_RE = re.compile(r"^\d+-")


def _profile_example(profile: str) -> Path:
    from ..labseed import PROFILE_EXAMPLE

    return EXAMPLES / PROFILE_EXAMPLE.get(profile, profile)


def _read_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text()) if path.is_file() else None
    return data if isinstance(data, dict) else {}


def _resources(example: Path, engine: str) -> dict[str, int]:
    """Nodes, RAM (MiB) and vCPUs of an example lab, from its definition (the
    platform's when the example has none) and its plan."""
    path = example / "definition.yaml"
    if not path.is_file():
        path = PLATFORMS / engine / "definition.yaml"
    definition = _read_yaml(path).get("definition") or {}
    resources = _read_yaml(example / "rodeo-plan.yaml").get("resources") or {}
    templates = definition.get("node_templates") or {}
    nodes = definition.get("nodes") or []
    mem = cpu = 0
    for node in nodes:
        flavor = (templates.get(node.get("template", "")) or {}).get("flavor", "")
        spec = resources.get(flavor) or {}
        mem += int(spec.get("memory_mib") or 0)
        cpu += int(spec.get("vcpu") or 0)
    return {"nodes": len(nodes), "memory_mib": mem, "vcpu": cpu}


def engines() -> dict[str, Any]:
    """Every engine with its base plan text (what a new rodeo starts from)."""
    out = []
    for name, spec in ENGINES.items():
        entry = {"name": name, **spec}
        if spec["base"]:
            example = _profile_example(spec["base"])
            entry["resources"] = _resources(example, name)
            entry["plan"] = (example / "rodeo-plan.yaml").read_text()
        else:
            entry["resources"] = {"nodes": 1, "memory_mib": 4096, "vcpu": 2}
            entry["definition"] = (PLATFORMS / name / "definition.yaml").read_text()
        out.append(entry)
    return {"engines": out, "capabilities": CAPABILITIES}


def _chapter_title(body: str, fallback: str) -> str:
    match = _HEADING_RE.search(body)
    return _TAG_RE.sub("", match.group(1)).strip() if match else fallback


def _chapters(story: Path) -> list[dict[str, Any]]:
    meta = {c.get("file"): c for c in (_read_yaml(story / "chapters.yaml").get("chapters") or [])
            if isinstance(c, dict)}
    out = []
    for path in sorted(story.glob("*.md")):
        body = path.read_text()
        cid = _NUMBER_PREFIX_RE.sub("", path.stem)
        info = meta.get(path.name, {})
        out.append({
            "id": cid,
            "file": path.name,
            "title": info.get("title") or _chapter_title(body, cid),
            "mins": int(info.get("mins") or DEFAULT_MINUTES),
            "needs": list(info.get("needs") or []),
            "check": info.get("check") or "",
            "spans": len(_SPAN_OPEN_RE.findall(_COMMENT_RE.sub("", body))),
            "body": body,
        })
    return out


def _variants(story: Path) -> dict[str, list[str]]:
    out = {}
    for path in sorted((story / "stories").glob("*.yaml")):
        ids = yaml.safe_load(path.read_text())
        out[path.stem] = [str(i) for i in ids] if isinstance(ids, list) else []
    return out


def workshops() -> dict[str, Any]:
    """Bundled examples that carry a story/ directory, with their chapters."""
    out = []
    for example in sorted(p for p in EXAMPLES.iterdir() if (p / "story").is_dir()):
        story = example / "story"
        meta = _read_yaml(story / "chapters.yaml")
        chapters = _chapters(story)
        if not chapters:
            continue
        out.append({
            "id": example.name,
            "title": meta.get("title") or example.name,
            "source": "rodeo/data/examples/{}/story".format(example.name),
            "engine": _read_yaml(example / "rodeo-plan.yaml").get("type") or "suse-virt",
            "chapters": chapters,
            "variants": _variants(story),
        })
    return {"workshops": out}


def labinabox_imports() -> list[dict[str, Any]]:
    """Bundled lab-in-a-box rodeos a new rodeo can start from."""
    from ..labseed import PROFILE_EXAMPLE

    out = []
    for profile, example_name in PROFILE_EXAMPLE.items():
        plan = _read_yaml(EXAMPLES / example_name / "rodeo-plan.yaml")
        if plan.get("type") != "lab-in-a-box":
            continue
        addons: set[str] = set()
        for node in ((plan.get("lab_in_a_box") or {}).get("nodes") or {}).values():
            for addon in (node or {}).get("addons") or []:
                addons.add(addon if isinstance(addon, str) else next(iter(addon)))
        out.append({"profile": profile, "title": plan.get("name") or profile,
                    "addons": sorted(addons),
                    "plan": (EXAMPLES / example_name / "rodeo-plan.yaml").read_text()})
    return out


_LIAB_COMPONENTS = r"""
import json, os, sys
repo = sys.argv[1]
os.environ["LABBUILDER_SCRIPTS_DIR"] = os.path.join(repo, "scripts")
os.environ["LABBUILDER_LIBS_DIR"] = os.path.join(repo, "libs")
os.environ["LABBUILDER_STATUS_FILE"] = os.devnull
sys.path.insert(0, os.path.join(repo, "webui", "lib"))
import api
status, body = api.dispatch("components", "GET", {}, b"")
if status != 200:
    sys.exit("components: " + str(body))
print(json.dumps([{"name": c["name"].removeprefix("install_"), "targets": c.get("targets", [])}
                  for c in body["components"]]))
"""


def labinabox_addons(checkout: Path) -> list[dict[str, Any]]:
    """Add-ons of a lab-in-a-box checkout, from its own lab-builder API: short name
    (as plans use it) and targets ("vm", "baremetal", "container", ...).

    Runs in a separate interpreter so lab-in-a-box's modules and environment
    never mix with rodeo's. Raises RuntimeError when the checkout can't answer.
    """
    r = subprocess.run([sys.executable, "-c", _LIAB_COMPONENTS, str(checkout)],
                       capture_output=True, text=True, env={**os.environ})
    if r.returncode != 0:
        raise RuntimeError("lab-in-a-box add-ons from {}: {}".format(checkout, r.stderr.strip()[-300:]))
    return json.loads(r.stdout)


def labinabox(checkout: Path | None = None, builder_url: str = LAB_BUILDER_URL,
              version: str = "") -> dict[str, Any]:
    """The lab-builder URL, the lab-in-a-box add-ons (empty without a checkout) and imports."""
    return {
        "builder_url": builder_url,
        "version": version,
        "addons": labinabox_addons(checkout) if checkout else [],
        "imports": labinabox_imports(),
    }
