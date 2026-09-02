#!/usr/bin/env python3
"""Render a chapter's assignment.md files into a single offline markdown
guide for a given locale, by resolving the same <span id="X"> template
placeholders the live Instruqt/rmstory build resolves against
rmstory/strings/<locale>/<id>/@default/content.json.

Usage:
    ./build_guide.py [--locale pt-BR] [--out offline-guides/.build/guide.md]
"""
import argparse
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

CHAPTER_DIRS = [
    "01-the-arrival-welcome",
    "02-the-subterranean-divide-cluster-prep",
    "03-the-flash-crash-first-vm",
    "04-the-rising-tide-live-migration",
    "05-the-invisible-intruder-networking",
    "06-the-unthinkable-error-snapshots",
    "07-the-stampede-automation",
    "08-a-new-horizon-whats-next",
]

_cache = {}
_missing = set()
_raw_terms = {}  # id -> literal inline English text, for lang="nolang" no spans


def get_string(strings_dir, sid):
    if sid in _cache:
        return _cache[sid]
    p = strings_dir / sid / "@default" / "content.json"
    if not p.exists():
        val = _raw_terms.get(sid)
        if val is None:
            _missing.add(sid)
        _cache[sid] = val
        return val
    data = json.loads(p.read_text())
    _cache[sid] = data.get("content", "")
    return _cache[sid]


SELF_CLOSE_RE = re.compile(r'<span\s+id="([^"]+)"[^>]*/>')


def expand(strings_dir, sid, _stack=None):
    _stack = _stack or set()
    if sid in _stack:
        return ""  # cycle guard
    content = get_string(strings_dir, sid)
    if content is None:
        return None

    def repl(m):
        child = m.group(1)
        val = expand(strings_dir, child, _stack | {sid})
        return val if val is not None else m.group(0)

    return SELF_CLOSE_RE.sub(repl, content)


# Match a span's opening tag regardless of attribute order (id is not
# always the first attribute, e.g. <span lang="en" id="X" ...>).
OPEN_RE = re.compile(r'<span\b(?=[^>]*\bid="([^"]+)")[^>]*>')
CLOSE_RE = re.compile(r'</span>')
TAG_RE = re.compile(r'(<span\b[^>]*>|</span>)')
ANY_OPEN_RE = re.compile(r'<span\b[^>]*>')


def render_body(strings_dir, text):
    """Walk text, replacing outermost <span id=X>...</span> blocks with
    localized (recursively expanded) content; leave everything else as-is.
    Spans without an id (pure styling, e.g. <span class="danger">) are
    tracked for nesting purposes but never substituted."""
    out = []
    pos = 0
    stack = []  # list of {"start": int, "id": str|None}
    for m in TAG_RE.finditer(text):
        tag = m.group(1)
        if tag.startswith("</span"):
            if not stack:
                continue  # stray close, ignore
            item = stack.pop()
            if not stack and item["id"] is not None:
                out.append(text[pos:item["start"]])
                val = expand(strings_dir, item["id"])
                if val is None:
                    inner = text[item["start"]:m.end()]
                    val = ANY_OPEN_RE.sub("", inner)
                    val = CLOSE_RE.sub("", val)
                out.append(val)
                pos = m.end()
        else:
            om = OPEN_RE.match(tag)
            sid = om.group(1) if om else None
            stack.append({"start": m.start(), "id": sid})
    out.append(text[pos:])
    return "".join(out)


STYLE_RE = re.compile(r'<style[^>]*>.*?</style>', re.S)


def clean_ui_chrome(text):
    """Strip Instruqt-only chrome that makes no sense in a static guide:
    embedded <style> blocks, tab-switch buttons, and sandbox variables."""
    text = STYLE_RE.sub("", text)
    text = re.sub(r'\[button label="([^"]+)"[^\]]*\]\(tab-\d+\)', r'**\1**', text)
    text = re.sub(
        r'\[\[\s*Instruqt-Var key="RANCHER_PASSWORD"[^\]]*\]\]',
        "(senha exibida no laboratório original — defina a sua)",
        text,
    )
    text = re.sub(
        r'\[\[\s*Instruqt-Var key="_SANDBOX_ID"[^\]]*\]\]', "SEU-SANDBOX-ID", text
    )
    text = re.sub(
        r'\[\[\s*Instruqt-Var[^\]]*\]\]', "(valor específico do laboratório)", text
    )
    return text


def split_frontmatter(raw):
    parts = raw.split("---", 2)
    if len(parts) >= 3:
        return parts[1], parts[2]
    return "", raw


SETEXT_RE = re.compile(r'^(?!#)(\S.*?)[ \t]*\n=+[ \t]*$', re.M)


def demote_headings(text):
    """The source markdown gives every section title (chapter AND every
    task/subsection within it) the same setext '====' underline, so pandoc
    would render them all as <h1>. Keep only the first one per chapter as
    the true chapter title (tagged .chapter-title, used for the PDF's
    running header); demote every later one to h2 ('----' underline) so
    heading size matches the actual document structure."""
    first_seen = False
    out = []
    pos = 0
    for m in SETEXT_RE.finditer(text):
        out.append(text[pos:m.start()])
        title = m.group(1)
        if not first_seen:
            first_seen = True
            out.append(f"{title} {{.chapter-title}}\n{'=' * max(3, len(title))}")
        else:
            out.append(f"{title}\n{'-' * max(3, len(title))}")
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def collect_raw_terms():
    """Scan every span in every chapter's English source and record its
    literal inner text, keyed by id. Spans marked lang="nolang" no (e.g.
    product/technology names that are never translated) have no
    content.json entry in ANY locale, so this is the fallback text used
    when a locale string is missing for one of them."""
    for d in CHAPTER_DIRS:
        raw = (REPO / d / "assignment.md").read_text()
        stack = []  # list of (id_or_None, start_of_inner)
        for m in TAG_RE.finditer(raw):
            tag = m.group(1)
            if tag.startswith("</span"):
                if stack:
                    sid, inner_start = stack.pop()
                    if sid is not None:
                        inner = raw[inner_start:m.start()]
                        inner = TAG_RE.sub("", inner)
                        _raw_terms.setdefault(sid, inner)
            else:
                om = OPEN_RE.match(tag)
                sid = om.group(1) if om else None
                stack.append((sid, m.end()))


def build(locale, out_path):
    strings_dir = REPO / "rmstory" / "strings" / locale
    if not strings_dir.is_dir():
        raise SystemExit(f"No strings found for locale {locale!r} at {strings_dir}")

    collect_raw_terms()
    chunks = []
    for d in CHAPTER_DIRS:
        raw = (REPO / d / "assignment.md").read_text()
        _fm, body = split_frontmatter(raw)
        rendered = render_body(strings_dir, body)
        rendered = clean_ui_chrome(rendered)
        rendered = demote_headings(rendered)
        # rewrite relative asset paths to absolute file:// paths so the
        # PDF renderer can locate images regardless of the md file location
        rendered = rendered.replace("../assets/", f"file://{REPO}/assets/")
        chunks.append(f"\n{rendered}\n")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(chunks))
    print(f"Wrote {out_path}")
    if _missing:
        print(f"Missing {len(_missing)} string ids (fell back to English):")
        for s in sorted(_missing):
            print(" -", s)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--locale", default="pt-BR", help="locale under rmstory/strings/ (default: pt-BR)")
    ap.add_argument("--out", type=Path, default=None, help="output .md path")
    args = ap.parse_args()
    out_path = args.out or REPO / "offline-guides" / ".build" / f"guide-{args.locale}.md"
    build(args.locale, out_path)


if __name__ == "__main__":
    main()
