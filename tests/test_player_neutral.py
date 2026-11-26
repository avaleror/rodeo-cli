"""The lab player describes itself on its own terms: no other lab platform's name.

Covers what students see (the page, the locked page), the player's docs and
help, and the player's own code and comments. The one allowed exception is
the track's own syntax the parser must match to read a track: the variable
tag (``runner._VAR``) and the hosted sandbox address (``runner._SANDBOX_TAG``, ``runner._SANDBOX_DOMAIN``). Names are kept as SHA-256 hashes so this file does not
spell them out either.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from click.testing import CliRunner

from rodeo.commands.player_cmd import player_cmd
from rodeo.player import runner, server

ROOT = Path(__file__).resolve().parents[1]
# sha256 of the lower-cased names that must not appear.
BANNED = {"658d3611caf0de6e9c69db128d2adb08ca08b075493357f145d648c9bfa98aba"}
WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")

SOURCES = [
    "rodeo/data/player/static/player.html",
    "rodeo/data/player/static/player.js",
    "docs/player.md",
    "rodeo/commands/player_cmd.py",
    "rodeo/fleet/player.py",
    *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "rodeo" / "player").glob("*.py")),
]


def _hits(text: str) -> list[str]:
    return sorted({w for w in WORD.findall(text)
                   if hashlib.sha256(w.lower().encode()).hexdigest() in BANNED})


def _without_track_syntax(text: str) -> str:
    for part in (runner._VAR.pattern, runner._SANDBOX_TAG, runner._SANDBOX_DOMAIN):
        text = text.replace(part, "")
    return text


def test_player_sources_name_no_other_lab_platform():
    found = {rel: _hits(_without_track_syntax((ROOT / rel).read_text()))
             for rel in SOURCES}
    assert {rel: w for rel, w in found.items() if w} == {}


def test_student_pages_and_help_name_no_other_lab_platform():
    texts = [server.LOCKED_PAGE, server.page("Lab")]
    cli = CliRunner()
    texts.append(cli.invoke(player_cmd, ["--help"]).output)
    for name in player_cmd.commands:
        texts.append(cli.invoke(player_cmd, [name, "--help"]).output)
    assert _hits("\n".join(texts)) == []


def test_the_guard_catches_the_name():
    # Built from parts so the guard itself stays honest without spelling it.
    sample = "a " + "ins" + "truqt" + "-like page"
    assert _hits(sample)
