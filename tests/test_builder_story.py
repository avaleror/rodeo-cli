"""Rodeo Builder: the rmstory work of the live server (rodeo/builder/storywork.py, api.py).

The ``fake_rmstory`` fixture (conftest.py) puts an ``rmstory`` on PATH that
answers like the real CLI.
"""
from __future__ import annotations

import json

import pytest

from rodeo.builder import storywork
from rodeo.builder.api import Api

CHAPTERS = [
    {"id": "intro", "body": '# Intro\n<span lang="en" id="intro.1">Saddle up</span>\n<span lang="en">New line</span>\n'},
    {"id": "ride", "body": '# Ride\nWARN\n<span lang="en" id="ride.1">Go</span>\n'},
]


def test_info_lists_engines_and_says_when_rmstory_is_missing(fake_rmstory, monkeypatch):
    info = storywork.info()
    assert info == {"available": True, "error": "", "engines": [{"name": "deepl", "generate": False},
                                                                {"name": "ollama", "generate": True}]}
    monkeypatch.setenv("PATH", "/nonexistent")
    missing = storywork.info()
    assert not missing["available"] and "install-deps --story" in missing["error"] and missing["engines"] == []


def test_validate_maps_findings_to_chapters(fake_rmstory):
    out = storywork.validate(CHAPTERS)
    assert out["warnings"] == [{"chapter": "ride", "line": 2, "message": "odd span", "error": False}]
    assert "tagged span" in out["summary"]
    stopped = storywork.validate([{"id": "bad", "body": "NOID"}])
    assert stopped["warnings"][0]["chapter"] == "bad" and stopped["warnings"][0]["error"]
    assert stopped["summary"] == "rmstory stopped at an error"


def test_chapter_files_follow_ids_and_fall_back_when_unsafe_or_repeated():
    names = storywork._chapter_files([("intro", ""), ("intro", ""), ("../x", ""), ("Bad Id", "")])
    assert names == ["intro.md", "chapter-2.md", "chapter-3.md", "chapter-4.md"]


def test_translate_fills_only_what_the_store_lacks(fake_rmstory):
    store = {"es": {"intro.1": {"content": "Ya revisado", "status": "reviewed", "updated_by": "me"}}}
    out = storywork.translate(CHAPTERS, "es", "deepl", store)
    es = out["strings"]["es"]
    assert es["intro.1"] == {"content": "Ya revisado", "status": "reviewed", "updated_by": "me"}
    assert es["intro.9"] == {"content": "[es] New line", "status": "draft", "updated_by": "rmstory.engines:deepl"}
    assert es["ride.1"]["content"] == "[es] Go"
    assert "en" not in out["strings"]
    bodies = {c["id"]: c["body"] for c in out["chapters"]}
    assert 'id="intro.9"' in bodies["intro"] and bodies["ride"] == CHAPTERS[1]["body"]
    assert "machine-translated" in out["log"]


@pytest.mark.parametrize("to,engine,store,match", [
    ("ES", "deepl", None, "language tag"),
    ("es", "nope", None, "unknown rmstory engine"),
    ("es", "deepl", {"es": {"../x": {"content": "x"}}}, "bad row"),
    ("es", "deepl", {"es": {"a.1": {"content": 3}}}, "bad row"),
    ("es", "deepl", ["x"], "translation store"),
])
def test_translate_refuses_bad_requests(fake_rmstory, to, engine, store, match):
    with pytest.raises(ValueError, match=match):
        storywork.translate(CHAPTERS, to, engine, store)


def test_translate_reports_rmstory_failures(fake_rmstory, monkeypatch):
    monkeypatch.setenv("FAKE_RMSTORY_FAIL", "1")
    with pytest.raises(storywork.StoryError, match="bad key"):
        storywork.translate(CHAPTERS, "es", "deepl")


def test_draft_needs_a_generating_engine_and_a_prompt(fake_rmstory):
    assert storywork.draft("Open the UI", "de", "ollama") == {"body": '<span lang="de" id="draft.one">Open the UI</span>\n'}
    with pytest.raises(ValueError, match="translate but not generate"):
        storywork.draft("Open the UI", "en", "deepl")
    with pytest.raises(ValueError, match="describe"):
        storywork.draft("  ", "en", "ollama")
    with pytest.raises(ValueError, match="longer"):
        storywork.draft("x" * (storywork.MAX_PROMPT + 1), "en", "ollama")


def test_chapters_are_checked():
    with pytest.raises(ValueError, match="no chapters"):
        storywork.validate([])
    with pytest.raises(ValueError, match="id and a body"):
        storywork.validate([{"id": "a"}])


def _post(api, action, payload):
    return api.dispatch(action, "POST", body=json.dumps(payload).encode())


def test_api_runs_rmstory_only_on_the_live_server(fake_rmstory):
    assert _post(Api(), "translate", {}) == (403, {"error": "rmstory translate needs the rodeo builder server"})
    live = Api(can_save=True)
    status, story = live.dispatch("story")
    assert status == 200 and story["available"]
    status, body = _post(live, "validate", {"chapters": CHAPTERS})
    assert status == 200 and body["warnings"][0]["chapter"] == "ride"
    status, body = _post(live, "translate", {"chapters": CHAPTERS, "to": "de", "engine": "deepl"})
    assert status == 200 and body["strings"]["de"]["intro.1"]["content"] == "[de] Saddle up"
    status, body = _post(live, "draft", {"prompt": "Log in", "engine": "ollama"})
    assert status == 200 and 'lang="en"' in body["body"]
    assert _post(live, "draft", {"prompt": "Log in", "engine": "deepl"})[0] == 400
    assert live.dispatch("validate", "POST", body=b"not json")[0] == 400
    assert live.dispatch("validate", "POST", body=b"[1]")[0] == 400


def test_api_says_when_rmstory_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    status, body = _post(Api(can_save=True), "validate", {"chapters": CHAPTERS})
    assert status == 502 and "not installed" in body["error"]
