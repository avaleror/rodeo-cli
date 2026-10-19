"""Import a workshop's chapters into the Rodeo Builder's library from a git URL.

``import_source(url)`` takes a git repository URL, or a GitHub-style
``https://<host>/<owner>/<repo>/tree/<ref>/<path>`` URL, clones only the files
chapters can be read from (shallow and sparse, like the chapter sources in
``chapter_sources.yaml``) and returns one library group in the shape
:func:`.discovery.workshops` uses. The format is detected, in this order:

- ``rmstory``: ``<path>/story/*.md`` (a rodeo profile's story, with its
  ``chapters.yaml`` and story variants), or ``<path>/*.md`` next to a
  ``chapters.yaml``;
- ``instruqt``: ``<path>/<challenge>/assignment.md``;
- ``markdown``: ``<path>/*.md`` (title from the first heading, ``**Time:** N min``).

Only public https repositories: the clone runs without credentials or prompts.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import discovery

_TREE_RE = re.compile(r"^/([^/]+)/([^/]+?)(?:\.git)?(?:/(?:tree|blob)/([^/]+)(?:/(.*))?)?/?$")
_REF_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]{0,199}$")
_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._ -]{0,127}$")
MAX_CHAPTERS = 200
TIMEOUT = 120


def parse_source_url(url: str) -> dict[str, str]:
    """``{"repo", "ref", "path"}`` from *url*. *ref* is "" for the default branch; in a
    ``/tree/<ref>/<path>`` URL the ref is one path segment (a branch with "/" in its
    name needs the plain repository URL and a top-level path). Raises ValueError."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("give an https:// URL of a public git repository")
    match = _TREE_RE.match(parsed.path)
    if not match or parsed.query or parsed.fragment:
        raise ValueError("expected https://<host>/<owner>/<repo>[/tree/<ref>/<path>]")
    owner, name, ref, path = match.group(1), match.group(2), match.group(3) or "", (match.group(4) or "").strip("/")
    if ref and not _REF_RE.match(ref):
        raise ValueError("{!r} is not a branch or tag name".format(ref))
    if path and not all(_PATH_SEGMENT_RE.match(seg) for seg in path.split("/")):
        raise ValueError("{!r} is not a plain path in the repository".format(path))
    return {"repo": "https://{}/{}/{}".format(parsed.netloc, owner, name), "ref": ref, "path": path}


def _sub(path: str, tail: str) -> str:
    return "/" + "/".join(p for p in (path, tail) if p)


def fetch_commands(src: dict[str, str], dest: Path) -> list[list[str]]:
    """Shallow, sparse clone of every file a chapter could be read from under *path*."""
    path = src["path"]
    patterns = [_sub(path, t) for t in ("*.md", "chapters.yaml", "*/assignment.md", "*/check-*",
                                        "story/*.md", "story/chapters.yaml", "story/stories/*.yaml")]
    clone = ["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse"]
    if src["ref"]:
        clone += ["--branch", src["ref"]]
    return [clone + ["--", src["repo"], str(dest)],
            ["git", "-C", str(dest), "sparse-checkout", "set", "--no-cone", *patterns]]


def detect_format(root: Path) -> tuple[str, Path]:
    """(format, chapters directory) for the fetched *root*, or ValueError."""
    if any((root / "story").glob("*.md")):
        return "rmstory", root / "story"
    if (root / "chapters.yaml").is_file() and any(root.glob("*.md")):
        return "rmstory", root
    if root.is_dir() and any((p / "assignment.md").is_file() for p in root.iterdir() if p.is_dir()):
        return "instruqt", root
    if any(root.glob("*.md")):
        return "markdown", root
    raise ValueError("no chapters found: expected story/*.md, <challenge>/assignment.md or *.md")


def _group_id(src: dict[str, str]) -> str:
    tail = "/".join(p for p in (urlparse(src["repo"]).path.strip("/"), src["path"]) if p)
    return "url-" + (re.sub(r"[^a-z0-9]+", "-", tail.lower()).strip("-")[:60] or "source")


def read_group(src: dict[str, str], checkout: Path) -> dict[str, Any]:
    """The library group of an already fetched source."""
    root = checkout / src["path"] if src["path"] else checkout
    fmt, directory = detect_format(root)
    title = src["path"].rsplit("/", 1)[-1] if src["path"] else urlparse(src["repo"]).path.rsplit("/", 1)[-1]
    variants: dict[str, list[str]] = {}
    if fmt == "rmstory":
        chapters = discovery._chapters(directory)
        variants = discovery._variants(directory)
        title = discovery._read_yaml(directory / "chapters.yaml").get("title") or title
    else:
        rel = str(directory.relative_to(checkout)) if directory != checkout else ""
        chapters = discovery.source_chapters({"format": fmt, "path": rel, "needs": []}, checkout)
    if not chapters:
        raise ValueError("no chapters found in {}".format(src["path"] or "the repository root"))
    if len(chapters) > MAX_CHAPTERS:
        raise ValueError("too many chapters ({} > {})".format(len(chapters), MAX_CHAPTERS))
    shown = src["repo"] + ("/tree/{}/{}".format(src["ref"] or "HEAD", src["path"]) if src["path"] else "")
    return {"id": _group_id(src), "title": title, "source": shown, "format": fmt, "engine": "", "profile": "",
            "plan": "", "chapters": chapters, "variants": variants, "imported": True}


def import_source(url: str, run: Any = subprocess.run) -> dict[str, Any]:
    """Fetch *url* and return its library group. Raises ValueError for a bad URL or a
    source without chapters, RuntimeError when git fails."""
    src = parse_source_url(url)
    with tempfile.TemporaryDirectory(prefix="rodeo-builder-import-") as tmp:
        dest = Path(tmp) / "src"
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/true", "GIT_CONFIG_NOSYSTEM": "1"}
        for cmd in fetch_commands(src, dest):
            try:
                r = run(cmd, capture_output=True, text=True, env=env, timeout=TIMEOUT)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("fetching {} took longer than {} s".format(src["repo"], TIMEOUT)) from exc
            if r.returncode != 0:
                raise RuntimeError("fetching {}: {}".format(src["repo"], (r.stderr or "").strip()[-300:]))
        return read_group(src, dest)
