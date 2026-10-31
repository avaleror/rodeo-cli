"""Data the Rodeo Builder shows, read from the rodeo-cli tree.

- engines: the four lab engines, their base profile, resources and capabilities
- workshops: bundled examples with a ``story/`` directory, and the chapter
  sources outside rodeo-cli listed in ``chapter_sources.yaml``
- labinabox: where the lab-in-a-box lab-builder lives, and its catalogue (add-ons,
  infrastructure services, Kubernetes cluster types)

Chapter metadata (minutes, needed capabilities, check script) comes from an
optional ``story/chapters.yaml`` beside the chapters; rmstory ignores it.

A catalog rodeo's translations come with its chapters: its ``story/strings/``
on the default branch, then each language branch's ``story/strings/<lang>/``
(:mod:`rodeo.langbranch`), which wins over the default branch's copy.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
from pathlib import Path
from typing import Any

import yaml

from .. import catalog, content, langbranch

DATA = Path(__file__).resolve().parent.parent / "data"
EXAMPLES = DATA / "examples"
PLATFORMS = DATA / "platforms"

LAB_BUILDER_URL = "https://rmahique.github.io/lab-in-a-box/lab-builder/"
DEFAULT_MINUTES = 10


_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)
_TAG_RE = re.compile(r"<[^>]+>")
_SPAN_OPEN_RE = re.compile(r"<span\b[^>]*?(?<!/)>")
_COMMENT_RE = re.compile(r"<!--.*?(?:-->|$)", re.S)
_NUMBER_PREFIX_RE = re.compile(r"^\d+-")
_LEADING_NUMBER_RE = re.compile(r"^(\d+)")
_FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
_TIME_RE = re.compile(r"^\*\*Time:\*\*\s*(\d+)\s*min", re.M | re.I)
_STRINGS_LEAF_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._:-]{0,127}/@default/content\.json$")
STRINGS_MAX_BYTES = 256 * 1024

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
    """Every rodeo type the builder offers (:attr:`.RodeoProfile.builder`), with its base
    plan text (what a new rodeo starts from); ``capabilities``, the union of what they
    provide; ``targets``, every registered ``deployment_target``; ``content_formats``,
    every instructions format a plan's ``content:`` takes. Plugins' are included."""
    from ..content import FORMATS
    from ..host_context import known_targets
    from ..profiles import registered_profiles

    out = []
    for profile in registered_profiles():
        if not profile.builder:
            continue
        name, spec = profile.name, {"base": None, "provides": [], **profile.builder}
        entry = {"name": name, **spec}
        if spec["base"]:
            example = _profile_example(spec["base"])
            entry["resources"] = _resources(example, name)
            entry["plan"] = (example / "rodeo-plan.yaml").read_text()
        else:
            entry["resources"] = {"nodes": 1, "memory_mib": 4096, "vcpu": 2}
            entry["definition"] = (PLATFORMS / name / "definition.yaml").read_text()
        out.append(entry)
    capabilities = sorted({c for e in out for c in e["provides"]})
    return {"engines": out, "capabilities": capabilities, "targets": known_targets(),
            "content_formats": list(FORMATS)}


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
            "repo": src["repo"],
            "source": "{}/tree/{}/{}".format(src["repo"].removesuffix(".git"), src["ref"] or "HEAD", src["path"]),
            "engine": src["engine"],
            "profile": src["profile"],
            "plan": _plan_text(_profile_example(src["profile"])) if src["profile"] else "",
            "chapters": source_chapters(src, checkout),
            "variants": _variants(checkout / src["path"].strip("/")) if src["format"] == "rmstory" else {},
            "strings": _strings(checkout / src["path"].strip("/") / "strings") if src.get("languages") else {},
        })
    return {"workshops": out}


def chapter_sources() -> list[dict[str, Any]]:
    """chapter_sources.yaml, with repo/ref/path taken from a profile's workshop:
    block where a source names one, then one ``rmstory`` source per catalog rodeo
    (:mod:`rodeo.catalog`) whose repository the file does not list: its ``story/``,
    id ``catalog-<name>``, no base profile, the default branch when the entry has no ref,
    ``languages`` true (its translations are read too)."""
    out = []
    for raw in _read_yaml(SOURCES_FILE).get("sources") or []:
        src = dict(raw)
        if src.get("workshop"):
            ws = _read_yaml(_profile_example(src["workshop"]) / "rodeo-plan.yaml").get("workshop") or {}
            src.setdefault("repo", ws.get("repo"))
            src.setdefault("ref", ws.get("ref") or ws.get("branch") or "main")
            src.setdefault("path", "tracks/{}".format(ws.get("track")))
        missing = [k for k in ("id", "title", "engine", "profile", "format", "repo", "ref", "path") if not src.get(k)]
        if missing or src["format"] not in content.FORMATS:
            raise ValueError("chapter source {}: missing {} or unknown format".format(src.get("id"), missing))
        out.append(src)
    listed = {s["repo"].removesuffix(".git").lower() for s in out}
    for name, entry in catalog.entries().items():
        if entry["repo"].removesuffix(".git").lower() in listed:
            continue
        out.append({"id": "catalog-" + name, "title": entry.get("title") or name, "engine": entry.get("engine") or "",
                    "profile": "", "format": "rmstory", "repo": entry["repo"], "ref": entry["ref"], "path": "story",
                    "needs": [], "languages": True})
    return out


# A cloned symlink is checked out as a plain file holding its target, so reading a
# chapter never follows it out of the clone (to /etc/passwd, /proc/self/environ...).
NO_SYMLINKS = ["-c", "core.symlinks=false"]


def source_fetch_commands(src: dict[str, Any], dest: Path) -> list[list[str]]:
    """Shallow, sparse clone of only the files a source's chapters are read from."""
    path = src["path"].strip("/")
    if src["format"] == "instruqt":
        patterns = ["/{}/*/assignment.md".format(path), "/{}/*/check-*".format(path)]
    elif src["format"] == "rmstory":
        patterns = ["/{}/*.md".format(path), "/{}/chapters.yaml".format(path), "/{}/stories/*.yaml".format(path)]
        if src.get("languages"):
            patterns.append("/{}/strings/*/*/@default/content.json".format(path))
    else:
        patterns = ["/{}/*.md".format(path)]
        if src.get("checks"):
            patterns.append("/{}/*".format(str(Path(src["checks"]).parent).strip("/")))
    return [
        ["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", *NO_SYMLINKS,
         *(["--branch", src["ref"]] if src["ref"] else []), "--", src["repo"], str(dest)],
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
            if src.get("languages"):
                errors.update(fetch_language_strings(src, target, run))
    return out, errors


def fetch_language_strings(src: dict[str, Any], checkout: Path, run: Any = subprocess.run) -> dict[str, str]:
    """Write each language branch's ``<path>/strings/<lang>/`` of *src* into its *checkout*
    (only rmstory store files, links left out). Returns the errors, keyed
    ``<source id> (<lang> branch)``; a branch without translations is no error."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    path = src["path"].strip("/")
    errors: dict[str, str] = {}
    try:
        branches = langbranch.remote_language_branches(src["repo"], run)
    except RuntimeError as exc:
        return {"{} (language branches)".format(src["id"]): str(exc)}
    for lang in branches:
        key = "{} ({} branch)".format(src["id"], lang)
        r = run(["git", "-C", str(checkout), "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin",
                 "refs/heads/" + lang], capture_output=True, text=True, env=env)
        if r.returncode != 0:
            errors[key] = (r.stderr or "").strip()[-300:]
            continue
        prefix = "{}/strings/{}/".format(path, lang)
        r = run(["git", "-C", str(checkout), "archive", "--format=tar", "FETCH_HEAD", "--", prefix],
                capture_output=True, env=env)
        if r.returncode != 0:
            if b"did not match" not in (r.stderr or b""):
                errors[key] = (r.stderr or b"").decode(errors="replace").strip()[-300:]
            continue
        dest = checkout / path / "strings" / lang
        if dest.exists():
            shutil.rmtree(dest)
        with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tar:
            for member in tar.getmembers():
                leaf = member.name.removeprefix(prefix)
                if not (member.isfile() and member.name.startswith(prefix) and _STRINGS_LEAF_RE.match(leaf)
                        and member.size <= STRINGS_MAX_BYTES):
                    continue
                target = dest / leaf
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(member).read())
    return errors


def _strings(root: Path) -> dict[str, dict[str, dict[str, str]]]:
    """The rmstory store under *root* as the builder's ``{lang: {span id: row}}``; unreadable
    rows and rmstory's own copies of the source text are left out."""
    from .storywork import _SOURCE_WRITER, STATUSES

    out: dict[str, dict[str, dict[str, str]]] = {}
    for leaf in sorted(root.glob("*/*/@default/content.json")):
        lang, span_id = leaf.parent.parent.parent.name, leaf.parent.parent.name
        try:
            record = json.loads(leaf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (not isinstance(record, dict) or not isinstance(record.get("content"), str)
                or not langbranch.LANG_RE.match(lang) or record.get("updated_by") == _SOURCE_WRITER):
            continue
        out.setdefault(lang, {})[span_id] = {
            "content": record["content"], "status": record.get("status") if record.get("status") in STATUSES else "draft",
            "updated_by": str(record.get("updated_by") or "")}
    return out


def catalog_rodeo(name: str, run: Any = subprocess.run) -> dict[str, Any]:
    """The catalog rodeo *name* as the page starts a rodeo from it: ``{"id", "title", "engine",
    "profile", "plan", "lab", "chapters", "variants", "strings", "warnings"}``.

    Fetched now (shallow, no credentials or prompts, links as plain files): *plan* is its
    ``rodeo-plan.yaml`` text, *lab* the plan's ``lab_in_a_box.lab`` (None without one),
    *profile* the base its ``builder.yaml`` names, *chapters* its ``story/`` chapters with
    their ``checks/check-<id>.sh``, *strings* its translations, language branches included
    (*warnings*: the branches that could not be read). Raises ValueError when *name* is not
    in the catalog or holds no plan, RuntimeError when the fetch fails.
    """
    entry = catalog.find(name)
    if entry is None:
        raise ValueError("{} is not in the rodeo catalog".format(name))
    src = {"id": "catalog-" + name, "repo": entry["repo"], "path": "story"}
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    with tempfile.TemporaryDirectory(prefix="rodeo-builder-catalog-") as tmp:
        dest = Path(tmp) / "src"
        r = run(["git", "clone", "-q", "--depth", "1", *NO_SYMLINKS, *(["--branch", entry["ref"]] if entry["ref"] else []),
                 "--", entry["repo"], str(dest)], capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise RuntimeError("fetching {}: {}".format(entry["repo"], (r.stderr or "").strip()[-300:]))
        warnings = list(fetch_language_strings(src, dest, run).values())
        plan_file = dest / "rodeo-plan.yaml"
        if not plan_file.is_file():
            raise ValueError("{} has no rodeo-plan.yaml".format(entry["repo"]))
        plan_text = plan_file.read_text(errors="replace")
        try:
            plan = yaml.safe_load(plan_text)
        except yaml.YAMLError as exc:
            raise ValueError("{}: rodeo-plan.yaml is not YAML ({})".format(entry["repo"], exc)) from None
        plan = plan if isinstance(plan, dict) else {}
        overlay = plan.get("lab_in_a_box") if isinstance(plan.get("lab_in_a_box"), dict) else {}
        story = dest / "story"
        chapters = _chapters(story) if story.is_dir() else []
        for ch in chapters:
            script = dest / "checks" / "check-{}.sh".format(ch["id"])
            if script.is_file():
                ch["check_script"] = script.read_text(errors="replace")
        return {"id": src["id"], "title": entry.get("title") or name,
                "engine": str(plan.get("type") or entry.get("engine") or ""),
                "profile": str(_read_yaml(dest / "builder.yaml").get("base") or ""), "plan": plan_text,
                "lab": overlay.get("lab") if isinstance(overlay.get("lab"), dict) else None,
                "chapters": chapters, "variants": _variants(story) if story.is_dir() else {},
                "strings": _strings(story / "strings"), "warnings": warnings}


def _check_script(directory: Path) -> str:
    scripts = sorted(directory.glob("check-*"))
    return scripts[0].read_text(errors="replace") if scripts else ""


def source_chapters(src: dict[str, Any], checkout: Path) -> list[dict[str, Any]]:
    """Chapters of one fetched source: Instruqt challenges, markdown files or a rodeo story/."""
    root = checkout / src["path"].strip("/")
    needs = list(src.get("needs") or [])
    if src["format"] == "rmstory":
        return [{**ch, "needs": ch["needs"] or needs} for ch in _chapters(root)]
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
    """Bundled lab-in-a-box rodeos a new rodeo can start from: profile, title, add-ons,
    plan text and the plan's lab_in_a_box.lab (None when it has none), ??placeholders kept."""
    from ..labseed import PROFILE_EXAMPLE

    out = []
    for profile, example_name in PROFILE_EXAMPLE.items():
        plan = _read_yaml(EXAMPLES / example_name / "rodeo-plan.yaml")
        if plan.get("type") != "lab-in-a-box":
            continue
        overlay = plan.get("lab_in_a_box") or {}
        lab = overlay.get("lab") if isinstance(overlay.get("lab"), dict) else None
        addons: set[str] = set()
        nodes = [*(overlay.get("nodes") or {}).values(), *((lab or {}).get("nodes") or {}).values()]
        for node in nodes:
            for addon in (node or {}).get("addons") or []:
                addons.add(addon if isinstance(addon, str) else next(iter(addon)))
        out.append({"profile": profile, "title": plan.get("name") or profile,
                    "addons": sorted(addons), "lab": lab,
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

# The checkout's schema, shaped like rodeo.labinabox_host.schema_from_outputs: the field
# names of the base sections and of each add-on (the base schema's other sections, such as
# pxe, count as add-ons).
_LIAB_SCHEMA = r"""
import json, os, sys
repo = sys.argv[1]
os.environ["LABBUILDER_SCRIPTS_DIR"] = os.path.join(repo, "scripts")
os.environ["LABBUILDER_LIBS_DIR"] = os.path.join(repo, "libs")
os.environ["LABBUILDER_STATUS_FILE"] = os.devnull
sys.path.insert(0, os.path.join(repo, "webui", "lib"))
import api
status, base = api.dispatch("base", "GET", {}, b"")
if status != 200:
    sys.exit("base: " + str(base))
names = lambda section: [f["name"] for f in (section or {}).get("fields", [])]
sections = base["sections"]
out = {"common": names(sections.get("common")), "nodes": names(sections.get("nodes")),
       "kclusters": names(sections.get("kclusters")),
       "addons": {k: names(v) for k, v in sections.items() if k not in ("common", "nodes", "kclusters")}}
status, body = api.dispatch("components", "GET", {}, b"")
for c in body.get("components", []) if status == 200 else []:
    if (c.get("kind") or "addon") != "addon":
        continue
    status, schema = api.dispatch("schema", "GET", {"name": [c["name"]]}, b"")
    if status == 200:
        out["addons"][c["name"].removeprefix("install_")] = names(schema)
print(json.dumps(out))
"""

LIAB_KINDS = {"addon": "addons", "infrastructure": "infrastructure", "kcluster": "kclusters"}
_SHEBANG_ENV = re.compile(r"^#!\s*/usr/bin/env\s+(\S+)")


def _addon_interpreter(checkout: Path) -> str:
    """The interpreter lab-in-a-box's add-on scripts name in their ``#!/usr/bin/env <name>``
    line (for example ``python3.11``), or "" when none does."""
    for script in sorted((checkout / "scripts").glob("install_*")):
        try:
            with script.open(errors="replace") as fh:
                match = _SHEBANG_ENV.match(fh.readline())
        except OSError:
            continue
        if match:
            return match.group(1)
    return ""


def _run_liab(script: str, checkout: Path) -> tuple["subprocess.CompletedProcess[str]", str]:
    """Run *script* (given the checkout's path) in a separate interpreter; returns the result and
    the interpreter name the add-on scripts' first line asks for ("" when none), which points at
    this Python for the run when it is not on PATH."""
    env = {**os.environ}
    with tempfile.TemporaryDirectory(prefix="rodeo-liab-python-") as shim:
        wanted = _addon_interpreter(checkout)
        if wanted and not shutil.which(wanted):
            os.symlink(sys.executable, os.path.join(shim, wanted))
            env["PATH"] = shim + os.pathsep + env.get("PATH", "")
        r = subprocess.run([sys.executable, "-c", script, str(checkout)], capture_output=True, text=True, env=env)
    return r, wanted


def labinabox_schema(checkout: Path) -> dict[str, Any]:
    """The schema of a lab-in-a-box checkout, from its own lab-builder API, in the shape
    :func:`rodeo.labinabox.unknown_fields` takes. Raises RuntimeError when the checkout can't answer."""
    r, _ = _run_liab(_LIAB_SCHEMA, checkout)
    if r.returncode != 0:
        raise RuntimeError("lab-in-a-box schema from {}: {}".format(checkout, r.stderr.strip()[-300:]))
    return json.loads(r.stdout)


def labinabox_addons(checkout: Path) -> list[dict[str, Any]]:
    """Catalogue of a lab-in-a-box checkout, from its own lab-builder API: short name
    (as plans use it), kind ("addon", "infrastructure" or "kcluster"; "addon" when
    the release does not report one) and targets ("vm", "baremetal", "container", ...).

    Runs in a separate interpreter so lab-in-a-box's modules and environment
    never mix with rodeo's. lab-in-a-box runs each add-on script for its schema and
    leaves out the ones it can't run, so when the interpreter their first line names
    is not on PATH, that name points at this Python for the run. Raises RuntimeError
    when the checkout can't answer or reports no add-ons.
    """
    r, wanted = _run_liab(_LIAB_COMPONENTS, checkout)
    if r.returncode != 0:
        raise RuntimeError("lab-in-a-box add-ons from {}: {}".format(checkout, r.stderr.strip()[-300:]))
    items = json.loads(r.stdout)
    if not any(item["kind"] == "addon" for item in items):
        raise RuntimeError("lab-in-a-box add-ons from {}: none reported (its add-on scripts could not "
                           "be run{})".format(checkout, " with " + wanted if wanted else ""))
    return items


# labinabox()'s catalogue and schema per checkout path, with the checkout_stamp() they were read at.
_liab_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
_liab_lock = threading.Lock()


def checkout_stamp(checkout: Path) -> tuple[int, int]:
    """``(newest mtime in ns, file count)`` of the parts of a lab-in-a-box checkout its
    lab-builder API reads (``scripts/``, ``libs/``, ``webui/lib/``): changes when they do."""
    newest = count = 0
    for sub in ("scripts", "libs", os.path.join("webui", "lib")):
        for root, dirs, files in os.walk(checkout / sub):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                with contextlib.suppress(OSError):
                    newest = max(newest, os.stat(os.path.join(root, name)).st_mtime_ns)
                    count += 1
    return newest, count


def labinabox(checkout: Path | None = None, builder_url: str = LAB_BUILDER_URL,
              version: str = "", missing: str = "") -> dict[str, Any]:
    """The lab-builder URL, the lab-in-a-box catalogue split by kind (``addons``,
    ``infrastructure``, ``kclusters``) and the bundled imports.

    ``error`` says why the catalogue is missing (*missing*, the caller's reason
    for having no checkout; or the checkout's API failed); it is empty when the
    catalogue was read. ``schema`` is the checkout's :func:`labinabox_schema`, or
    None with ``schema_error`` saying why. A complete answer (catalogue and schema) is kept
    per checkout and read again only when its :func:`checkout_stamp` changes.
    """
    out: dict[str, Any] = {"builder_url": builder_url, "version": version, "error": "",
                           "imports": labinabox_imports(), **{key: [] for key in LIAB_KINDS.values()},
                           "schema": None, "schema_error": ""}
    if checkout is None:
        out["error"] = missing or "no lab-in-a-box checkout was given, so its catalogue is unknown"
        return out
    key, stamp = str(checkout), checkout_stamp(checkout)
    with _liab_lock:
        kept = _liab_cache.get(key)
    if kept and kept[0] == stamp:
        return {**out, **copy.deepcopy(kept[1])}
    try:
        items = labinabox_addons(checkout)
    except (OSError, RuntimeError, ValueError) as exc:
        out["error"] = str(exc)
        return out
    for item in items:
        out[LIAB_KINDS.get(item["kind"], "addons")].append(item)
    try:
        out["schema"] = labinabox_schema(checkout)
    except (OSError, RuntimeError, ValueError) as exc:
        out["schema_error"] = str(exc)
        return out
    with _liab_lock:
        _liab_cache[key] = (stamp, copy.deepcopy({k: out[k] for k in ("schema", *LIAB_KINDS.values())}))
    return out
