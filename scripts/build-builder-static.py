#!/usr/bin/env python3
"""Build the static, backend-free Rodeo Builder (published with the docs on GitHub Pages).

Copies rodeo/builder/htdocs/ to the output directory and embeds, in its index.html,
what the builder API (rodeo/builder/api.py) answers for every GET the page makes,
computed from this checkout. An inline script replaces app.js's apiGet() with
lookups over that data.

Usage: build-builder-static.py [--output DIR] [--labinabox CHECKOUT]
                               [--labinabox-version REF] [--lab-builder-url URL]
                               [--source ID=CHECKOUT ...] [--no-fetch-sources]

--labinabox points at a lab-in-a-box checkout; its add-ons fill the add-on picker.
Without it the picker accepts free text.

The chapter sources in rodeo/builder/chapter_sources.yaml are fetched (sparse,
shallow git clones) unless --no-fetch-sources; --source uses a local checkout
for one source instead.

Exits non-zero when any API answer fails or no engine is found, so a page with
missing data is never written.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HTDOCS = REPO / "rodeo" / "builder" / "htdocs"
sys.path.insert(0, str(REPO))

from rodeo.builder.api import Api  # noqa: E402
from rodeo.builder.discovery import LAB_BUILDER_URL, chapter_sources, source_fetch_commands  # noqa: E402

STATIC_API = """
  <script id="static-api-data" type="application/json">%s</script>
  <script>
  (function () {
    const data = JSON.parse(document.getElementById("static-api-data").textContent);
    window.apiGet = async function (action) {
      if (!Object.prototype.hasOwnProperty.call(data, action)) throw new Error(action + " needs the builder server");
      return JSON.parse(JSON.stringify(data[action]));
    };
  })();
  </script>
"""


def version() -> str:
    """rodeo-cli version from pyproject.toml, plus the commit when git knows it."""
    match = re.search(r'^version\s*=\s*"([^"]+)"', (REPO / "pyproject.toml").read_text(), re.M)
    base = match.group(1) if match else "unknown"
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True)
    except OSError:
        return base
    return base + ("+" + r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "")


def fetch_sources(dest: Path, given: dict[str, Path]) -> dict[str, Path]:
    """Checkout per chapter source: the given ones, the rest cloned into dest."""
    out = dict(given)
    for src in chapter_sources():
        if src["id"] in out:
            continue
        target = dest / src["id"]
        for cmd in source_fetch_commands(src, target):
            r = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            if r.returncode != 0:
                sys.exit("build-builder-static: fetching chapter source {}: {}".format(src["id"], r.stderr.strip()[-300:]))
        out[src["id"]] = target
    return out


def build(output: Path, data: dict, rodeo_version: str) -> None:
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(HTDOCS, output, ignore=shutil.ignore_patterns("__pycache__"))
    html = (HTDOCS / "index.html").read_text().replace("__RODEOVERSION__", rodeo_version)
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    body_end = html.rindex("</body>")
    (output / "index.html").write_text(html[:body_end] + STATIC_API % payload + html[body_end:])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--output", type=Path, default=REPO / "docs" / "builder",
                        help="directory to write (default: docs/builder, published by mkdocs)")
    parser.add_argument("--labinabox", type=Path, help="lab-in-a-box checkout for the add-on list")
    parser.add_argument("--labinabox-version", default="", help="lab-in-a-box ref the checkout is at")
    parser.add_argument("--lab-builder-url", default=LAB_BUILDER_URL, help="published lab-in-a-box lab-builder")
    parser.add_argument("--source", action="append", default=[], metavar="ID=CHECKOUT",
                        help="local checkout for one chapter source (repeatable)")
    parser.add_argument("--no-fetch-sources", action="store_true",
                        help="list only the chapter sources given with --source")
    args = parser.parse_args()

    given = {}
    for item in args.source:
        sid, sep, path = item.partition("=")
        if not sep or not path:
            sys.exit("build-builder-static: --source wants ID=CHECKOUT, got {!r}".format(item))
        given[sid] = Path(path)
    with tempfile.TemporaryDirectory(prefix="rodeo-builder-sources-") as tmp:
        sources = given if args.no_fetch_sources else fetch_sources(Path(tmp), given)
        api = Api(args.labinabox, args.lab_builder_url, args.labinabox_version, sources)
        try:
            data = api.static_data()
        except RuntimeError as exc:
            sys.exit("build-builder-static: {}".format(exc))
    if not data["engines"]["engines"]:
        sys.exit("build-builder-static: no engines found")
    rodeo_version = version()
    build(args.output, data, rodeo_version)
    chapters = sum(len(w["chapters"]) for w in data["workshops"]["workshops"])
    print("built {}: {} engines, {} workshops, {} chapters, {} lab-in-a-box add-ons, version {}".format(
        args.output, len(data["engines"]["engines"]), len(data["workshops"]["workshops"]), chapters,
        len(data["labinabox"]["addons"]), rodeo_version), file=sys.stderr)


if __name__ == "__main__":
    main()
