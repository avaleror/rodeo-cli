"""Chapter outline: sections and steps read from the track's own headings."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from rodeo.player.outline import chapter_name, classify, clean_title, outline, split_h1

CHAPTER = """[[ seed line ]]

⚡ Chapter 3: The Flash Crash
=============================

<div class="story">The markets are melting down.</div>

## Your objectives

1. Verify the image

\U0001F510 Login Credentials
====================

Username: admin

What is a VM image?
===================

Background before the first task.

\U0001F4C0 Task 1: Verify the operating system image
================================================

```txt
Not a heading
=============
# not a heading either
```

\U0001F680 Task 2: Provision the calculation engine
===============================================

Create it.

# Task 3: Access the Web Console

Open the console.

\U0001F3CB️ Bonus Drills: see through the abstraction (optional, for the curious)
============================================================

kubectl get vm

\U0001F4BC Why does this matter?
=========================

Business value.

\U0001F4DA More information
===================

- a link
"""


def test_titles_lose_leading_emoji_and_tags():
    assert clean_title("⚡ Chapter 3: The Flash Crash") == "Chapter 3: The Flash Crash"
    assert clean_title('<span id="a" lang="en">\U0001F3E6 Chapter 1: The Arrival</span>') == "Chapter 1: The Arrival"
    assert chapter_name('<span lang="en">⚡ Chapter 3: The Flash Crash</span>') == (3, "The Flash Crash")
    assert chapter_name("Getting started") == (None, "Getting started")


def test_split_ignores_headings_inside_code_and_lower_levels():
    pre, parts = split_h1(CHAPTER)
    assert pre == "[[ seed line ]]"
    titles = [clean_title(h) for h, _ in parts]
    assert titles[0] == "Chapter 3: The Flash Crash"
    assert "Not a heading" not in titles and "not a heading either" not in titles
    assert "Your objectives" not in titles  # an h2 stays inside the cover
    assert "Task 3: Access the Web Console" in titles  # ATX h1 counts


@pytest.mark.parametrize("title,first,want", [
    ("Chapter 2: The Divide", True, ("cover", None, "The Divide")),
    ("Chapter 2: The Divide", False, ("section", None, "Chapter 2: The Divide")),
    ("\U0001F510 Login Credentials", False, ("credentials", None, "Login Credentials")),
    ("Task 4: Build a storage class", False, ("task", 4, "Build a storage class")),
    ("Step 2 - Open the UI", False, ("task", 2, "Open the UI")),
    ("Bonus Drill: validate storage (optional)", False, ("optional", None, "Bonus Drill: validate storage")),
    ("Task 5: Extra mile (optional)", False, ("optional", None, "Extra mile")),
    ("Why does this matter?", False, ("why", None, "Why does this matter?")),
    ("More information", False, ("more", None, "More information")),
    ("What is Longhorn?", False, ("section", None, "What is Longhorn?")),
])
def test_classify(title, first, want):
    assert classify(title, first=first) == want


def test_outline_kinds_and_steps():
    o = outline(CHAPTER)
    kinds = [s["kind"] for s in o["sections"]]
    assert kinds == ["cover", "credentials", "section", "task", "task", "task", "optional", "why", "more"]
    assert o["sections"][0]["markdown"].startswith("[[ seed line ]]")  # preamble joins the first section
    assert "Not a heading" in o["sections"][3]["markdown"]
    steps = o["steps"]
    assert [(s["kind"], s["number"], s["name"]) for s in steps] == [
        ("task", 1, "Verify the operating system image"),
        ("task", 2, "Provision the calculation engine"),
        ("task", 3, "Access the Web Console"),
        ("optional", None, "Bonus Drills: see through the abstraction"),
    ]
    assert steps[0]["sections"] == [2, 3]  # the background section introduces task 1


def test_a_chapter_without_task_headings_is_one_step():
    o = outline("Closing words\n=============\n\nThanks.\n\nNext steps\n==========\n\nMore.\n")
    assert [s["kind"] for s in o["sections"]] == ["section", "section"]
    assert o["steps"] == [{"kind": "task", "name": "", "number": 1, "sections": [0, 1]}]


def test_a_chapter_with_no_headings_at_all_is_one_step():
    o = outline("Just some text.\n")
    assert o["sections"][0]["markdown"] == "Just some text."
    assert o["steps"][0]["sections"] == [0]


# Resolved at import: test fixtures may point HOME elsewhere while a test runs.
_TRACK = Path(os.environ.get("RODEO_PLAYER_TRACK", Path.home() / "GitHub" / "suse-virt-rodeo"))
REAL_TRACK = _TRACK if (_TRACK / "03-the-flash-crash-first-vm" / "assignment.md").is_file() else None


@pytest.mark.skipif(REAL_TRACK is None, reason="set RODEO_PLAYER_TRACK to a suse-virt-rodeo checkout")
def test_real_track_every_chapter_has_a_cover_and_steps():
    from rodeo.player.runner import load_track, render_body, strip_rmstory_spans

    for ch in load_track(REAL_TRACK):
        number, name = chapter_name(ch.title)
        assert number == ch.index + 1 and name
        o = outline(render_body(strip_rmstory_spans(ch.body), {}))
        assert o["sections"][0]["kind"] == "cover"
        assert o["steps"]
        if number < 8:  # chapter 8 is the wrap-up, with no tasks
            assert all(s["name"] for s in o["steps"])
            assert {"why", "more"} <= {s["kind"] for s in o["sections"]}
