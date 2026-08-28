"""Workshop story rendering — languages and story variants via rmstory.

The lab/config dir owns the narrative in ``story/``:

  story/*.md       rmstory-tagged markdown sources (authored in English)
  story/stories/   story-variant indexes, one ``<id>.yaml`` per variant
  story/strings/   the translation store (rmstory's filesystem backend)

``rodeo story render`` drives the ``rmstory`` CLI (the public surface of the
rmstory system package — see rodeo/storydeps.py) in two steps: translate the
sources to the requested language, then assemble the requested story variant.
The result is Jinja-rendered with deployment facts from the plan/inventory
(:func:`story_facts`), so one story source serves every topology.

Authoring rule for facts: put Jinja expressions inside invariant spans —
``<span no>{{ rancher_url }}</span>`` — so translation never touches them
(rmstory never translates ``no`` spans).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import ConfigError
from .inventory import build_inventory
from .storydeps import INSTALL_HINT

# Sources are authored in this language; translation runs only when the
# requested language differs.
SOURCE_LANGUAGE = "en"


def story_dir(cfg: dict) -> Path | None:
    """The lab's story/ directory, or None when the lab has no narrative.

    Prefers cfg["config_dir"]; falls back to the detected lab dir so the
    command works from inside a lab that has a plan but no definition.yaml
    (config_dir is only auto-set when a definition exists).
    """
    root = cfg.get("config_dir")
    if not root:
        from .config import find_lab_dir

        detected = find_lab_dir()
        root = str(detected) if detected else None
    if not root:
        return None
    candidate = Path(root) / "story"
    return candidate if candidate.is_dir() else None


def story_facts(cfg: dict) -> dict:
    """Deployment facts exposed to story templates.

    Keep this dict stable — it is the contract story authors write against.
    """
    net = cfg.get("network", {})
    vip = net.get("vip", "")
    rancher_ip = net.get("rancher_ip", "")
    nodeport = int(net.get("rancher_nodeport", 30002))
    try:
        vm_nodes = build_inventory(cfg).get("vm_nodes", [])
        vms = {n["name"]: {"ip": n.get("ip", "")} for n in vm_nodes}
    except Exception:
        vms = {
            name: {"ip": spec.get("ip", "")}
            for name, spec in cfg.get("vms", {}).items()
            if isinstance(spec, dict)
        }
    story_cfg = cfg.get("story", {}) if isinstance(cfg.get("story"), dict) else {}
    return {
        "name": cfg.get("name", ""),
        "type": cfg.get("type", ""),
        "language": story_cfg.get("language", SOURCE_LANGUAGE),
        "vip": vip,
        "harvester_url": f"https://{vip}" if vip else "",
        "rancher_ip": rancher_ip,
        "rancher_nodeport": nodeport,
        "rancher_url": f"https://{rancher_ip}:{nodeport}" if rancher_ip else "",
        "dns_domain": net.get("dns_domain", ""),
        "gateway": net.get("gateway", ""),
        "vms": vms,
        "vm_names": list(vms),
        "credentials": cfg.get("credentials", {}),
    }


def _rmstory_bin() -> str:
    binary = shutil.which("rmstory")
    if not binary:
        raise ConfigError(f"the 'rmstory' command is not installed — {INSTALL_HINT}")
    return binary


def _rmstory_env(story_root: Path, engine_env: dict | None) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("RMSTORY_STORIES_PATH", str(story_root / "stories"))
    env.setdefault("RMSTORY_STRINGS_PATH", str(story_root / "strings"))
    for key, value in (engine_env or {}).items():
        if value:
            env[str(key)] = str(value)
    return env


def _run_rmstory(args: list[str], env: dict[str, str]) -> None:
    cmd = [_rmstory_bin(), *args]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip().splitlines()
        tail = "\n".join(detail[-6:])
        raise ConfigError(f"rmstory {args[0]} failed (exit {r.returncode}):\n{tail}")


def render_story(
    cfg: dict,
    *,
    language: str | None = None,
    story_id: str | None = None,
    engine: str | None = None,
) -> str:
    """Return the rendered workshop hand-out for this lab.

    Precedence for the knobs: explicit argument > plan ``story:`` block >
    defaults (source language, all spans, no machine translation).
    """
    story_cfg = cfg.get("story", {}) if isinstance(cfg.get("story"), dict) else {}
    language = (language or story_cfg.get("language") or SOURCE_LANGUAGE).strip()
    story_id = story_id or story_cfg.get("id") or ""
    engine = engine or story_cfg.get("engine") or ""

    root = story_dir(cfg)
    if root is None:
        raise ConfigError(
            "this lab has no story/ directory — create <lab>/story/ with "
            "rmstory-tagged markdown (plus story/stories/ for variants); "
            "see docs/reference/plan.md#story"
        )
    sources = sorted(root.glob("*.md"))
    if not sources:
        raise ConfigError(f"no story sources found: {root}/*.md is empty")

    env = _rmstory_env(root, story_cfg.get("engine_env"))

    with tempfile.TemporaryDirectory(prefix="rodeo-story-") as tmp:
        tmp_path = Path(tmp)

        files = [str(p) for p in sources]
        if language != SOURCE_LANGUAGE:
            out_dir = tmp_path / "translated"
            out_dir.mkdir()
            args = ["translate", *files, "--to", language, "--out", str(out_dir)]
            if engine:
                args += ["--engine", engine]
            _run_rmstory(args, env)
            translated = sorted(out_dir.glob("*.md"))
            if not translated:
                raise ConfigError(
                    f"rmstory translate produced no files in {out_dir} — "
                    "check the story sources and translation store"
                )
            files = [str(p) for p in translated]

        if story_id:
            out_file = tmp_path / "assembled.md"
            _run_rmstory(["story", *files, "--story", story_id, "--out", str(out_file)], env)
            text = out_file.read_text()
        else:
            text = "\n".join(Path(f).read_text() for f in files)

    return _render_facts(text, cfg)


def _render_facts(text: str, cfg: dict) -> str:
    """Substitute {{ fact }} placeholders; fail closed on unknown names."""
    if "{{" not in text and "{%" not in text:
        return text
    import jinja2

    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    try:
        return env.from_string(text).render(**story_facts(cfg))
    except jinja2.UndefinedError as exc:
        raise ConfigError(
            f"story references an unknown deployment fact: {exc.message}\n"
            "Available facts: " + ", ".join(sorted(story_facts(cfg)))
        )
    except jinja2.TemplateSyntaxError as exc:
        raise ConfigError(f"story template syntax error (line {exc.lineno}): {exc.message}")
