"""rmstory work the live Rodeo Builder does on the page's behalf.

- :func:`info`: whether the ``rmstory`` command is installed, and its engines
  (each can translate; some can also generate text).
- :func:`validate`: ``rmstory validate`` on the rodeo's chapters, warnings per chapter.
- :func:`translate`: ``rmstory extract --engine <e> --to <lang>`` on the chapters,
  starting from the page's translation store; returns the filled store.
- :func:`draft`: ``rmstory generate new``, a tagged-span draft from a prompt.

Chapters arrive as ``[{"id", "body"}]`` and are written to a temporary
directory; nothing is kept on the server. Engines read their credentials from
the environment ``rodeo builder`` runs in (for example ``DEEPL_AUTH_KEY``,
``GEMINI_API_KEY``, ``RMSTORY_CLAUDE_MODEL``): see ``rmstory engines`` and the
rmstory engine modules. A translation store is
``{lang: {span_id: {"content", "status", "updated_by"}}}``, the rows of rmstory's
filesystem store (``<strings>/<lang>/<span_id>/@default/content.json``).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..storydeps import INSTALL_HINT

LANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})*$")
_CHAPTER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SPAN_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._:-]{0,127}$")
STATUSES = ("draft", "reviewed", "published")
MAX_CHAPTERS = 200
MAX_BODY = 1024 * 1024
MAX_PROMPT = 8000
TIMEOUT = 30 * 60
_ENGINE_LINE = re.compile(r"^(\S+)\s+(translate only|translate \+ generate)\s*$")
_FINDING_LINE = re.compile(r"^(warning|error): (.+?):(\d+): (.*)$")
_SOURCE_WRITER = "rmstory.extract"


class StoryError(RuntimeError):
    """rmstory is missing, refused the request, or failed."""


def _binary() -> str:
    binary = shutil.which("rmstory")
    if not binary:
        raise StoryError("the rmstory command is not installed on the builder's host: " + INSTALL_HINT)
    return binary


def _run(args: list[str], env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        r = subprocess.run([_binary(), *args], capture_output=True, text=True, env=env, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise StoryError("rmstory {} took longer than {} minutes".format(args[0], TIMEOUT // 60)) from exc
    if check and r.returncode != 0:
        tail = "\n".join((r.stderr or r.stdout).strip().splitlines()[-6:])
        raise StoryError("rmstory {} failed (exit {}): {}".format(args[0], r.returncode, tail))
    return r


def info() -> dict[str, Any]:
    """``{"available", "error", "engines": [{"name", "generate"}]}``; *error* says why
    rmstory can't be used (not installed, or ``rmstory engines`` failed)."""
    try:
        out = _run(["engines"]).stdout
    except StoryError as exc:
        return {"available": False, "error": str(exc), "engines": []}
    engines = []
    for line in out.splitlines():
        match = _ENGINE_LINE.match(line.strip())
        if match:
            engines.append({"name": match.group(1), "generate": match.group(2).endswith("generate")})
    return {"available": True, "error": "", "engines": engines}


def _engine(name: str, generate: bool = False) -> str:
    known = {e["name"]: e for e in info()["engines"]}
    if name not in known:
        raise ValueError("unknown rmstory engine {!r} (rmstory engines lists: {})".format(name, ", ".join(sorted(known))))
    if generate and not known[name]["generate"]:
        raise ValueError("rmstory engine {!r} can translate but not generate text".format(name))
    return name


def _language(lang: str) -> str:
    if not LANG_RE.match(lang or ""):
        raise ValueError("{!r} is not a language tag such as de or pt-br".format(lang))
    return lang


def _chapters(chapters: Any) -> list[tuple[str, str]]:
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("no chapters were sent")
    if len(chapters) > MAX_CHAPTERS:
        raise ValueError("too many chapters ({} > {})".format(len(chapters), MAX_CHAPTERS))
    out = []
    for ch in chapters:
        if not isinstance(ch, dict) or not isinstance(ch.get("body"), str):
            raise ValueError("each chapter needs an id and a body")
        if len(ch["body"].encode("utf-8")) > MAX_BODY:
            raise ValueError("chapter {} is larger than 1 MiB".format(ch.get("id")))
        out.append((str(ch.get("id") or ""), ch["body"]))
    return out


def _chapter_files(chapters: list[tuple[str, str]]) -> list[str]:
    """One file name per chapter: ``<chapter id>.md``, so the ids rmstory assigns are
    ``<chapter id>.N``; ``chapter-<n>.md`` when the id is not a plain, unique name."""
    names: list[str] = []
    for i, (cid, _) in enumerate(chapters):
        name = cid + ".md" if _CHAPTER_ID_RE.match(cid) else ""
        names.append(name if name and name not in names else "chapter-{}.md".format(i + 1))
    return names


def _write_chapters(chapters: list[tuple[str, str]], directory: Path) -> dict[str, int]:
    """Write the chapters (see :func:`_chapter_files`); returns {file name: chapter index}."""
    directory.mkdir(parents=True)
    names = {}
    for i, (name, (_, body)) in enumerate(zip(_chapter_files(chapters), chapters)):
        (directory / name).write_text(body, encoding="utf-8")
        names[name] = i
    return names


def validate(chapters: Any) -> dict[str, Any]:
    """rmstory's own findings: ``{"warnings": [{"chapter", "line", "message", "error"}], "summary"}``.
    rmstory stops at its first error (for example a span with no id), so an error
    finding may hide later ones."""
    items = _chapters(chapters)
    with tempfile.TemporaryDirectory(prefix="rodeo-builder-story-") as tmp:
        src = Path(tmp) / "story"
        names = _write_chapters(items, src)
        r = _run(["validate", str(src)], check=False)
    warnings = []
    for line in r.stderr.splitlines():
        match = _FINDING_LINE.match(line.strip())
        if match and Path(match.group(2)).name in names:
            warnings.append({"chapter": items[names[Path(match.group(2)).name]][0], "line": int(match.group(3)),
                             "message": match.group(4), "error": match.group(1) == "error"})
    if r.returncode != 0 and not any(w["error"] for w in warnings):
        tail = "\n".join((r.stderr or r.stdout).strip().splitlines()[-6:])
        raise StoryError("rmstory validate failed (exit {}): {}".format(r.returncode, tail))
    summary = r.stdout.strip() or "rmstory stopped at an error"
    return {"warnings": warnings, "summary": summary}


def _write_store(store: Any, root: Path) -> None:
    if not isinstance(store, dict):
        raise ValueError("the translation store must be {language: {span id: row}}")
    stamp = datetime.now(timezone.utc).isoformat()
    for lang, rows in store.items():
        _language(lang)
        if not isinstance(rows, dict):
            raise ValueError("translation store: {} must map span ids to rows".format(lang))
        for span_id, row in rows.items():
            if not SPAN_ID_RE.match(span_id) or not isinstance(row, dict) or not isinstance(row.get("content"), str):
                raise ValueError("translation store: bad row {}/{}".format(lang, span_id))
            status = row.get("status") if row.get("status") in STATUSES else "draft"
            leaf = root / lang / span_id / "@default"
            leaf.mkdir(parents=True, exist_ok=True)
            (leaf / "content.json").write_text(json.dumps({
                "content": row["content"], "original_language": None, "status": status,
                "source_checksum": None, "updated_by": str(row.get("updated_by") or "rodeo-builder"),
                "date_updated": stamp}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_store(root: Path) -> dict[str, dict[str, dict[str, str]]]:
    """Every translated row under *root*, leaving out rmstory's copies of the source text."""
    store: dict[str, dict[str, dict[str, str]]] = {}
    for leaf in sorted(root.glob("*/*/@default/content.json")):
        record = json.loads(leaf.read_text(encoding="utf-8"))
        if record.get("updated_by") == _SOURCE_WRITER:
            continue
        lang, span_id = leaf.parent.parent.parent.name, leaf.parent.parent.name
        store.setdefault(lang, {})[span_id] = {"content": record["content"], "status": record.get("status") or "draft",
                                               "updated_by": record.get("updated_by") or ""}
    return store


def translate(chapters: Any, to: str, engine: str, store: Any = None) -> dict[str, Any]:
    """Fill the *to* translations the store lacks with *engine*. Returns
    ``{"strings": store, "chapters": [{"id", "body"}], "log"}``; *chapters* are the
    bodies after rmstory gave every unlabelled span an id."""
    items = _chapters(chapters)
    to, engine = _language(to), _engine(engine)
    with tempfile.TemporaryDirectory(prefix="rodeo-builder-story-") as tmp:
        root = Path(tmp)
        src, strings = root / "story", root / "strings"
        names = _write_chapters(items, src)
        _write_store(store or {}, strings)
        env = {**os.environ, "RMSTORY_STRINGS_PATH": str(strings), "RMSTORY_STORIES_PATH": str(root / "stories")}
        env.pop("MULTILANG_DB_BACKEND", None)
        env.pop("MULTILANG_DB_PATH", None)
        r = _run(["extract", str(src), "--stories-dir", str(root / "stories"), "--engine", engine, "--to", to], env)
        bodies = [{"id": items[i][0], "body": (src / name).read_text(encoding="utf-8")}
                  for name, i in names.items()]
        return {"strings": _read_store(strings), "chapters": bodies, "log": r.stdout.strip()}


def draft(prompt: str, lang: str, engine: str) -> dict[str, str]:
    """A tagged-span draft from *prompt*: ``{"body"}``."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("describe the chapter to draft")
    if len(prompt) > MAX_PROMPT:
        raise ValueError("the description is longer than {} characters".format(MAX_PROMPT))
    lang, engine = _language(lang), _engine(engine, generate=True)
    r = _run(["generate", "new", "--engine", engine, "--prompt", prompt, "--lang", lang])
    body = r.stdout.strip()
    if not body:
        raise StoryError("rmstory generate returned nothing")
    return {"body": body + "\n"}
