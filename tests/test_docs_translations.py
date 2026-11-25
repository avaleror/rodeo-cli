"""Translated docs pages keep up with their English page.

Every section heading (h2-h4) of an English page must exist in each of its
translations (``page.<lang>.md``), matched by anchor: translated headings keep
the English anchor with ``{ #anchor }``. A section added in English and not yet
translated fails here, so a translation cannot fall behind silently. Pages with
no translation at all are not checked; they fall back to English.

Anchors are computed like MkDocs' ``toc`` extension does (default slugify,
``_1`` suffixes for repeats) so the test needs no MkDocs install.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "docs"
_LANG_SUFFIX = re.compile(r"\.([a-z]{2}(?:-[A-Z]{2})?)\.md$")
_HEADING = re.compile(r"^(#{2,4})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_ATTR_ID = re.compile(r"\{[^}]*#([^\s}]+)[^}]*\}\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def _heading_text(raw: str) -> str:
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", raw)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("`", "").replace("*", "")


def anchors(path: Path) -> list[str]:
    """The h2-h4 anchors of a Markdown page, in order, as MkDocs makes them."""
    out: list[str] = []
    seen: set[str] = set()
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        m = None if in_fence else _HEADING.match(line)
        if not m:
            continue
        raw = m.group(2)
        explicit = _ATTR_ID.search(raw)
        if explicit:
            anchor = explicit.group(1)
        else:
            anchor = base = _slugify(_heading_text(raw))
            n = 1
            while anchor in seen:
                anchor = f"{base}_{n}"
                n += 1
        seen.add(anchor)
        out.append(anchor)
    return out


def _translations() -> list[tuple[Path, Path]]:
    pairs = []
    for page in sorted(DOCS.rglob("*.md")):
        m = _LANG_SUFFIX.search(page.name)
        if m:
            pairs.append((page.with_name(page.name[: m.start()] + ".md"), page))
    return pairs


@pytest.mark.parametrize(
    "english, translated", _translations(), ids=lambda p: str(p.relative_to(DOCS))
)
def test_translation_has_every_english_section(english, translated):
    assert english.exists(), f"{translated.name} has no English page {english.name}"
    have = set(anchors(translated))
    missing = [a for a in anchors(english) if a not in have]
    assert not missing, (
        f"{translated.relative_to(DOCS)} lacks these sections of {english.name} "
        f"(translate them, keeping the anchor with {{ #anchor }}): {missing}"
    )


def test_anchors_follow_mkdocs_slugify(tmp_path):
    page = tmp_path / "p.md"
    page.write_text(
        "## Step 6: deploy\n"
        "## `rodeo up --target aws`\n"
        "```bash\n## not a heading\n```\n"
        "### Shared accounts: who owns what (AWS and GCP)\n"
        "## Step 6: deploy\n"
        "## 部署 { #deploy-zh }\n",
        encoding="utf-8",
    )
    assert anchors(page) == [
        "step-6-deploy",
        "rodeo-up-target-aws",
        "shared-accounts-who-owns-what-aws-and-gcp",
        "step-6-deploy_1",
        "deploy-zh",
    ]
