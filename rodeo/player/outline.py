"""A chapter's outline: its sections and the steps a student works through.

Track authors already split a chapter with top-level headings (``Title`` over
``=====``, or ``# Title``). This module reads that structure as it is, with
generic rules, so any rodeo track gets the cover / steps / wrap-up flow:

- the first section, when its heading is ``Chapter N: Name``: the cover;
- ``... credentials``: pinned at the top of the guide;
- ``Task N: ...`` / ``Step N: ...``: one step each;
- ``Bonus ...`` or ``... (optional)``: an optional step;
- ``Why ...``: the wrap-up ("why does this matter");
- ``More information`` / ``Learn more`` / ``Resources``: wrap-up links;
- anything else stays a plain section, in reading order.

A chapter with no task headings becomes one step holding all its sections.
Pure functions on the rendered Markdown (variables already substituted); the
browser turns each section's Markdown into HTML.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

KINDS = ("cover", "credentials", "task", "optional", "why", "more", "section")

_TAG = re.compile(r"<[^>]+>")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SETEXT_H1 = re.compile(r"^=+\s*$")
_ATX_H1 = re.compile(r"^#(?!#)\s+(.+?)\s*#*\s*$")
# Leading emoji, pictographs, variation selectors, punctuation: anything up to
# the first letter or digit.
_LEADING_SYMBOLS = re.compile(r"^[^\w(]+", re.UNICODE)
_CHAPTER = re.compile(r"^chapter\s+(\d+)\s*[:.\-–—]\s*(.+)$", re.I)
_NUMBERED = re.compile(r"^(?:task|step|exercise|part)\s+(\d+)\s*[:.\-–—]?\s*(.*)$", re.I)
_OPTIONAL = re.compile(r"^bonus\b|\(optional\b", re.I)
_CREDENTIALS = re.compile(r"\bcredentials\b", re.I)
_WHY = re.compile(r"^why\b", re.I)
_MORE = re.compile(r"^(?:more information|learn more|further reading|resources|references)\b", re.I)


@dataclass(frozen=True)
class Section:
    kind: str
    title: str          # heading without leading symbols ("" for a chapter with no headings)
    name: str           # what the student reads in the rail: "Verify the operating system image"
    number: int | None  # "Task 2" -> 2
    markdown: str       # the section's body, heading excluded

    def as_dict(self) -> dict:
        return {"kind": self.kind, "title": self.title, "name": self.name,
                "number": self.number, "markdown": self.markdown}


def clean_title(text: str) -> str:
    """``⚡ Chapter 3: The Flash Crash`` -> ``Chapter 3: The Flash Crash`` (tags dropped too)."""
    text = html.unescape(_TAG.sub("", text))
    return _LEADING_SYMBOLS.sub("", text.strip()).strip()


def chapter_name(title: str) -> tuple[int | None, str]:
    """(number, name) from a chapter title: ``Chapter 3: The Flash Crash`` -> (3, 'The Flash Crash')."""
    t = clean_title(title)
    m = _CHAPTER.match(t)
    return (int(m.group(1)), m.group(2).strip()) if m else (None, t)


def split_h1(markdown: str) -> tuple[str, list[tuple[str, str]]]:
    """(text before the first heading, [(heading, body), ...]) on top-level headings only."""
    lines = markdown.split("\n")
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        heading = None
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if line.strip() and _SETEXT_H1.match(nxt) and not line.lstrip().startswith(("<", "|", "-", "*", ">")):
                heading = line.strip()
                i += 1  # skip the ===== line
            else:
                m = _ATX_H1.match(line)
                if m:
                    heading = m.group(1)
        if heading is not None:
            sections.append((heading, []))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
        i += 1
    return "\n".join(preamble).strip("\n"), [(h, "\n".join(b).strip("\n")) for h, b in sections]


def classify(title: str, *, first: bool) -> tuple[str, int | None, str]:
    """(kind, number, name) for one heading."""
    t = clean_title(title)
    if first and _CHAPTER.match(t):
        return "cover", None, chapter_name(t)[1]
    if _CREDENTIALS.search(t):
        return "credentials", None, t
    m = _NUMBERED.match(t)
    if _OPTIONAL.search(t):
        name = m.group(2) if m and m.group(2) else t
        return "optional", None, re.sub(r"\s*\(optional[^)]*\)\s*$", "", name, flags=re.I).strip() or t
    if m:
        return "task", int(m.group(1)), (m.group(2) or t).strip()
    if _WHY.match(t):
        return "why", None, t
    if _MORE.match(t):
        return "more", None, t
    return "section", None, t


def outline(markdown: str) -> dict:
    """Sections and steps of one rendered chapter.

    ``steps`` lists, per step, the indexes of its sections: one per task or
    optional section; or, when the chapter has none, a single step with every
    section that is not the cover, credentials or wrap-up.
    """
    preamble, parts = split_h1(markdown)
    sections: list[Section] = []
    for i, (heading, body) in enumerate(parts):
        kind, number, name = classify(heading, first=(i == 0))
        sections.append(Section(kind, clean_title(heading), name, number, body))
    if not sections:
        sections.append(Section("section", "", "", None, preamble))
        preamble = ""
    elif preamble.strip():
        # Text before the first heading (often a variable seed line) joins the first section.
        s0 = sections[0]
        sections[0] = Section(s0.kind, s0.title, s0.name, s0.number, preamble + "\n\n" + s0.markdown)

    steps: list[dict] = []
    pending: list[int] = []  # plain sections wait for the step they introduce
    for i, s in enumerate(sections):
        if s.kind == "section":
            pending.append(i)
        elif s.kind in ("task", "optional"):
            steps.append({"kind": s.kind, "name": s.name, "number": s.number, "sections": [*pending, i]})
            pending = []
    if not steps:
        steps = [{"kind": "task", "name": "", "number": 1, "sections": pending or [len(sections) - 1]}]
    elif pending:
        steps[-1]["sections"].extend(pending)  # trailing plain sections close the last step
    return {"sections": [s.as_dict() for s in sections], "steps": steps}
