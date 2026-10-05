#!/usr/bin/env python3
"""Build the static, backend-free Rodeo Builder (published with the docs on GitHub Pages).

Copies rodeo/builder/htdocs/ to the output directory and embeds, in its index.html,
what the builder API (rodeo/builder/api.py) answers for every GET the page makes,
computed from this checkout. An inline script replaces app.js's apiGet() with
lookups over that data.

Usage: build-builder-static.py [--output DIR] [--labinabox CHECKOUT]
                               [--labinabox-version REF] [--lab-builder-url URL]

--labinabox points at a lab-in-a-box checkout; its add-ons fill the add-on picker.
Without it the picker accepts free text.

Exits non-zero when any API answer fails or no engine is found, so a page with
missing data is never written.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HTDOCS = REPO / "rodeo" / "builder" / "htdocs"
sys.path.insert(0, str(REPO))

from rodeo.builder.api import Api  # noqa: E402
from rodeo.builder.discovery import LAB_BUILDER_URL  # noqa: E402

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
    args = parser.parse_args()

    api = Api(args.labinabox, args.lab_builder_url, args.labinabox_version)
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
