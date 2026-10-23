"""A lab's instructions, kept apart from its infrastructure.

A plan's optional ``content:`` block names where the instructions live:

    content:
      repo: https://github.com/<owner>/<repo>
      ref: main             # branch, tag or commit; default: the default branch
      path: story           # directory in the repository; default: the root
      format: rmstory       # rmstory | instruqt | markdown
      facts:                # instruqt: values for [[ Instruqt-Var key="..." ]]
        SMLM_URL: https://smlm.rodeo.lab
      replace:              # literal text swaps when rendering
        "example.com": rodeo.lab

:func:`apply` fetches the content into ``<lab>/content/`` (``<lab>/content.lock``
records the commit) and renders the guide into ``<lab>/guide/`` without touching
the deployed environment, so the instructions can be updated on a running lab.
An rmstory content is also what ``rodeo story render`` reads when the lab has no
``story/`` of its own. The plan's ``workshop:`` block and an in-profile ``story/``
work as before.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import yaml

from .config import ConfigError
from .labinabox_host import fetch_commands
from .workshop import render_text

FORMATS = ("rmstory", "instruqt", "markdown")
CHECKOUT = "content"
LOCK = "content.lock"
GUIDE = "guide"
TIMEOUT = 300
_REF_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]{0,199}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._ -]{0,127}$")

Run = Callable[..., "subprocess.CompletedProcess[str]"]


def content_spec(cfg: dict) -> dict[str, Any] | None:
    """Validated ``content:`` block, or None when the plan has none."""
    spec = cfg.get("content")
    if not spec:
        return None
    if not isinstance(spec, dict):
        raise ConfigError("content: must be a mapping with repo, format and optionally ref and path")
    repo, ref = str(spec.get("repo") or ""), str(spec.get("ref") or "")
    path, fmt = str(spec.get("path") or "").strip("/"), str(spec.get("format") or "")
    if not repo.startswith("https://") or "@" in repo.split("://", 1)[1].split("/", 1)[0]:
        raise ConfigError("content.repo must be an https:// git URL without credentials")
    if ref and not _REF_RE.match(ref):
        raise ConfigError(f"content.ref '{ref}' is not a branch, tag or commit")
    if path and not all(_SEGMENT_RE.match(seg) for seg in path.split("/")):
        raise ConfigError(f"content.path '{path}' is not a plain path in the repository")
    if fmt not in FORMATS:
        raise ConfigError(f"content.format must be one of {', '.join(FORMATS)}")
    return {"repo": repo, "ref": ref, "path": path, "format": fmt,
            "facts": {str(k): str(v) for k, v in (spec.get("facts") or {}).items()},
            "replace": {str(k): str(v) for k, v in (spec.get("replace") or {}).items()}}


def content_root(lab: Path, spec: dict[str, Any]) -> Path:
    """The fetched content's directory (``<lab>/content/<path>``)."""
    return lab / CHECKOUT / spec["path"] if spec["path"] else lab / CHECKOUT


def fetch(spec: dict[str, Any], lab: Path, run: Run = subprocess.run) -> str:
    """Fetch the content into ``<lab>/content/`` (replacing what was there); returns the commit."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/true"}
    lab.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".content.", dir=lab))
    try:
        dest = work / CHECKOUT
        cmds = fetch_commands(spec["repo"], spec["ref"] or "HEAD", dest)
        # A symlink in the repository is checked out as a plain file holding its target,
        # so rendering can't copy a file of this machine into the guide.
        cmds.insert(1, ["git", "-C", str(dest), "config", "core.symlinks", "false"])
        for cmd in cmds:
            try:
                r = run(cmd, capture_output=True, text=True, env=env, timeout=TIMEOUT)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"fetching {spec['repo']} took longer than {TIMEOUT} s") from exc
            if r.returncode != 0:
                raise RuntimeError(f"fetching {spec['repo']}: {(r.stderr or r.stdout).strip()[-300:]}")
        r = run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True, env=env, timeout=TIMEOUT)
        commit = r.stdout.strip() if r.returncode == 0 else ""
        shutil.rmtree(dest / ".git", ignore_errors=True)
        if not content_root(work, spec).is_dir():
            raise ConfigError(f"{spec['repo']} has no directory '{spec['path']}'")
        target = lab / CHECKOUT
        if target.exists():
            shutil.rmtree(target)
        dest.rename(target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    (lab / LOCK).write_text(yaml.safe_dump({"repo": spec["repo"], "ref": spec["ref"] or None, "path": spec["path"],
                                            "format": spec["format"], "commit": commit}, sort_keys=False))
    return commit


def _write_private(dest: Path, text: str) -> Path:
    dest.touch(mode=0o600, exist_ok=True)
    dest.chmod(0o600)
    dest.write_text(text)
    return dest


def _replace(text: str, spec: dict[str, Any]) -> str:
    for old, new in spec["replace"].items():
        text = text.replace(old, new)
    return text


def render(spec: dict[str, Any], lab: Path, cfg: dict) -> list[Path]:
    """Write the guide into ``<lab>/guide/`` (owner-only: facts can hold lab passwords)."""
    root = content_root(lab, spec)
    out = lab / GUIDE
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(mode=0o700, parents=True)
    written: list[Path] = []
    if spec["format"] == "instruqt":
        for challenge in sorted(p for p in root.iterdir() if (p / "assignment.md").is_file()):
            text = render_text((challenge / "assignment.md").read_text(errors="replace"), spec)
            written.append(_write_private(out / f"{challenge.name}.md", text))
    elif spec["format"] == "markdown":
        for source in sorted(root.glob("*.md")):
            written.append(_write_private(out / source.name, _replace(source.read_text(errors="replace"), spec)))
    else:
        from .story import render_story

        text = render_story(cfg, root=root)
        written.append(_write_private(out / "guide.md", _replace(text, spec)))
    if not written:
        raise ConfigError(f"no {spec['format']} chapters found in {root}")
    return written


def apply(cfg: dict, lab: Path, run: Run = subprocess.run) -> dict[str, Any]:
    """Fetch the plan's content and render the guide; ``{"commit", "files"}``.
    Raises ConfigError when the plan has no ``content:`` block."""
    spec = content_spec(cfg)
    if spec is None:
        raise ConfigError("this plan has no content: block (see docs/reference/plan.md#content)")
    commit = fetch(spec, lab, run)
    return {"commit": commit, "files": render(spec, lab, cfg)}

