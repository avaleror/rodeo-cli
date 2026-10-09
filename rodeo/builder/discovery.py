"""Data the Rodeo Builder shows, read from the rodeo-cli tree.

- engines: the four lab engines, their base profile, resources and capabilities
- workshops: bundled examples with a ``story/`` directory, and the chapter
  sources outside rodeo-cli listed in ``chapter_sources.yaml``
- labinabox: where the lab-in-a-box lab-builder lives, and its catalogue (add-ons,
  infrastructure services, Kubernetes cluster types)

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
        "note": "Rancher Prime on K3s plus K3s and RKE2 clusters it provisions.",
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

_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)
_TAG_RE = re.compile(r"<[^>]+>")
_SPAN_OPEN_RE = re.compile(r"<span\b[^>]*?(?<!/)>")
_COMMENT_RE = re.compile(r"<!--.*?(?:-->|$)", re.S)
_NUMBER_PREFIX_RE = re.compile(r"^\d+-")
_LEADING_NUMBER_RE = re.compile(r"^(\d+)")
_FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
_TIME_RE = re.compile(r"^\*\*Time:\*\*\s*(\d+)\s*min", re.M | re.I)

SOURCES_FILE = Path(__file__).resolve().parent / "chapter_sources.yaml"
# Instruqt's timelimit is an upper bound, not a duration: challenges get this.
INSTRUQT_MINUTES = 20


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
    capabilities = sorted({c for spec in ENGINES.values() for c in spec["provides"]})
    return {"engines": out, "capabilities": capabilities}


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
            "check_script": "",
            "spans": _span_count(body),
            "body": body,
        })
    return out


def _span_count(body: str) -> int:
    return len(_SPAN_OPEN_RE.findall(_COMMENT_RE.sub("", body)))


def _plan_text(example: Path) -> str:
    plan = example / "rodeo-plan.yaml"
    return plan.read_text() if plan.is_file() else ""


def _profile_of(example: Path) -> str:
    from ..labseed import PROFILE_EXAMPLE

    return next((p for p, e in PROFILE_EXAMPLE.items() if e == example.name), example.name)


def _variants(story: Path) -> dict[str, list[str]]:
    out = {}
    for path in sorted((story / "stories").glob("*.yaml")):
        ids = yaml.safe_load(path.read_text())
        out[path.stem] = [str(i) for i in ids] if isinstance(ids, list) else []
    return out


def workshops(sources: dict[str, Path] | None = None) -> dict[str, Any]:
    """Bundled examples that carry a story/ directory, then every chapter source
    with a checkout in *sources* (source id → checkout directory)."""
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
            "profile": _profile_of(example),
            "plan": _plan_text(example),
            "chapters": chapters,
            "variants": _variants(story),
        })
    for src in chapter_sources() if sources else []:
        checkout = sources.get(src["id"])
        if checkout is None:
            continue
        out.append({
            "id": src["id"],
            "title": src["title"],
            "source": "{}/tree/{}/{}".format(src["repo"].removesuffix(".git"), src["ref"], src["path"]),
            "engine": src["engine"],
            "profile": src["profile"],
            "plan": _plan_text(_profile_example(src["profile"])),
            "chapters": source_chapters(src, checkout),
            "variants": {},
        })
    return {"workshops": out}


def chapter_sources() -> list[dict[str, Any]]:
    """chapter_sources.yaml, with repo/ref/path taken from a profile's workshop:
    block where a source names one."""
    out = []
    for raw in _read_yaml(SOURCES_FILE).get("sources") or []:
        src = dict(raw)
        if src.get("workshop"):
            ws = _read_yaml(_profile_example(src["workshop"]) / "rodeo-plan.yaml").get("workshop") or {}
            src.setdefault("repo", ws.get("repo"))
            src.setdefault("ref", ws.get("ref") or ws.get("branch") or "main")
            src.setdefault("path", "tracks/{}".format(ws.get("track")))
        missing = [k for k in ("id", "title", "engine", "profile", "format", "repo", "ref", "path") if not src.get(k)]
        if missing or src["format"] not in ("instruqt", "markdown"):
            raise ValueError("chapter source {}: missing {} or unknown format".format(src.get("id"), missing))
        out.append(src)
    return out


def source_fetch_commands(src: dict[str, Any], dest: Path) -> list[list[str]]:
    """Shallow, sparse clone of only the files a source's chapters are read from."""
    path = src["path"].strip("/")
    if src["format"] == "instruqt":
        patterns = ["/{}/*/assignment.md".format(path), "/{}/*/check-*".format(path)]
    else:
        patterns = ["/{}/*.md".format(path)]
        if src.get("checks"):
            patterns.append("/{}/*".format(str(Path(src["checks"]).parent).strip("/")))
    return [
        ["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse",
         "--branch", src["ref"], src["repo"], str(dest)],
        ["git", "-C", str(dest), "sparse-checkout", "set", "--no-cone", *patterns],
    ]


def fetch_sources(dest: Path, given: dict[str, Path] | None = None,
                  run: Any = subprocess.run) -> tuple[dict[str, Path], dict[str, str]]:
    """Checkout per chapter source: the *given* ones, the rest cloned into *dest*.

    Returns (checkouts, errors): a source whose clone fails is left out of the
    checkouts and its git error tail is in errors, keyed by source id.
    """
    out = dict(given or {})
    errors: dict[str, str] = {}
    for src in chapter_sources():
        if src["id"] in out:
            continue
        target = dest / src["id"]
        for cmd in source_fetch_commands(src, target):
            r = run(cmd, capture_output=True, text=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            if r.returncode != 0:
                errors[src["id"]] = (r.stderr or "").strip()[-300:]
                break
        else:
            out[src["id"]] = target
    return out, errors


def _check_script(directory: Path) -> str:
    scripts = sorted(directory.glob("check-*"))
    return scripts[0].read_text(errors="replace") if scripts else ""


def source_chapters(src: dict[str, Any], checkout: Path) -> list[dict[str, Any]]:
    """Chapters of one fetched source: Instruqt challenges or markdown files."""
    root = checkout / src["path"].strip("/")
    needs = list(src.get("needs") or [])
    out = []
    if src["format"] == "instruqt":
        for challenge in sorted(p for p in root.iterdir() if (p / "assignment.md").is_file()):
            text = (challenge / "assignment.md").read_text(errors="replace")
            match = _FRONT_MATTER_RE.match(text)
            meta = yaml.safe_load(match.group(1)) if match else {}
            body = text[match.end():] if match else text
            cid = _NUMBER_PREFIX_RE.sub("", challenge.name)
            script = _check_script(challenge)
            out.append({"id": cid, "file": challenge.name + "/assignment.md",
                        "title": (meta or {}).get("title") or _chapter_title(body, cid),
                        "mins": INSTRUQT_MINUTES, "needs": needs, "check": bool(script), "check_script": script,
                        "spans": _span_count(body), "body": body.lstrip("\n")})
        return out
    for path in sorted(root.glob("*.md")):
        body = path.read_text(errors="replace")
        cid = _NUMBER_PREFIX_RE.sub("", path.stem)
        time = _TIME_RE.search(body)
        number = _LEADING_NUMBER_RE.match(path.stem)
        script = ""
        if src.get("checks") and number:
            check = checkout / src["checks"].format(n=int(number.group(1)))
            script = check.read_text(errors="replace") if check.is_file() else ""
        out.append({"id": cid, "file": path.name, "title": _chapter_title(body, cid),
                    "mins": int(time.group(1)) if time else DEFAULT_MINUTES, "needs": needs,
                    "check": bool(script), "check_script": script, "spans": _span_count(body), "body": body})
    return out


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
print(json.dumps([{"name": c["name"].removeprefix("install_"), "kind": c.get("kind") or "addon",
                   "targets": c.get("targets", [])} for c in body["components"]]))
"""

LIAB_KINDS = {"addon": "addons", "infrastructure": "infrastructure", "kcluster": "kclusters"}


def labinabox_addons(checkout: Path) -> list[dict[str, Any]]:
    """Catalogue of a lab-in-a-box checkout, from its own lab-builder API: short name
    (as plans use it), kind ("addon", "infrastructure" or "kcluster"; "addon" when
    the release does not report one) and targets ("vm", "baremetal", "container", ...).

    Runs in a separate interpreter so lab-in-a-box's modules and environment
    never mix with rodeo's. Raises RuntimeError when the checkout can't answer.
    """
    r = subprocess.run([sys.executable, "-c", _LIAB_COMPONENTS, str(checkout)],
                       capture_output=True, text=True, env={**os.environ})
    if r.returncode != 0:
        raise RuntimeError("lab-in-a-box add-ons from {}: {}".format(checkout, r.stderr.strip()[-300:]))
    return json.loads(r.stdout)


def labinabox(checkout: Path | None = None, builder_url: str = LAB_BUILDER_URL,
              version: str = "", missing: str = "") -> dict[str, Any]:
    """The lab-builder URL, the lab-in-a-box catalogue split by kind (``addons``,
    ``infrastructure``, ``kclusters``) and the bundled imports.

    ``error`` says why the catalogue is missing (*missing*, the caller's reason
    for having no checkout; or the checkout's API failed); it is empty when the
    catalogue was read.
    """
    out: dict[str, Any] = {"builder_url": builder_url, "version": version, "error": "",
                           "imports": labinabox_imports(), **{key: [] for key in LIAB_KINDS.values()}}
    if checkout is None:
        out["error"] = missing or "no lab-in-a-box checkout was given, so its catalogue is unknown"
        return out
    try:
        items = labinabox_addons(checkout)
    except (OSError, RuntimeError, ValueError) as exc:
        out["error"] = str(exc)
        return out
    for item in items:
        out[LIAB_KINDS.get(item["kind"], "addons")].append(item)
    return out
