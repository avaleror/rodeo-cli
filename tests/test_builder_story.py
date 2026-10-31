"""Rodeo Builder: the rmstory work of the live server (rodeo/builder/storywork.py, api.py).

The ``fake_rmstory`` fixture (conftest.py) puts an ``rmstory`` on PATH that
answers like the real CLI.
"""
from __future__ import annotations

import json

import pytest

from rodeo import labinabox_host
from rodeo.builder import storywork, vault
from rodeo.builder.api import Api

CHAPTERS = [
    {"id": "intro", "body": '# Intro\n<span lang="en" id="intro.1">Saddle up</span>\n<span lang="en">New line</span>\n'},
    {"id": "ride", "body": '# Ride\nWARN\n<span lang="en" id="ride.1">Go</span>\n'},
]


def test_info_lists_engines_and_says_when_rmstory_is_missing(fake_rmstory, monkeypatch):
    monkeypatch.delenv("DEEPL_AUTH_KEY", raising=False)
    info = storywork.info()
    assert info == {"available": True, "error": "", "engines": [
        {"name": "deepl", "generate": False, "credential": "deepl", "env": False},
        {"name": "ollama", "generate": True, "credential": None, "env": True}]}
    monkeypatch.setenv("DEEPL_AUTH_KEY", "from-env")
    assert storywork.info()["engines"][0]["env"] is True
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


def test_retell_rewrites_only_spans_with_ids(fake_rmstory):
    out = storywork.retell(CHAPTERS + [{"id": "plain", "body": "# Plain\n`kubectl get nodes`\n"}], "a space heist", "ollama")
    bodies = {c["id"]: c["body"] for c in out["chapters"]}
    assert bodies["intro"] == '# Intro\n<span lang="en" id="intro.1">a space heist</span>\n<span lang="en">New line</span>\n'
    assert bodies["ride"] == '# Ride\nWARN\n<span lang="en" id="ride.1">a space heist</span>\n'
    assert bodies["plain"] == "# Plain\n`kubectl get nodes`\n"
    assert [c["id"] for c in out["chapters"]] == ["intro", "ride", "plain"]


@pytest.mark.parametrize("theme,engine,match", [
    ("", "ollama", "what the new story is about"),
    ("x" * (storywork.MAX_THEME + 1), "ollama", "longer"),
    ("pirates", "deepl", "translate but not generate"),
])
def test_retell_refuses_bad_requests(fake_rmstory, theme, engine, match):
    with pytest.raises(ValueError, match=match):
        storywork.retell(CHAPTERS, theme, engine)


def test_api_retells_on_the_live_server(fake_rmstory):
    assert _post(Api(), "retell", {}) == (403, {"error": "rmstory retell needs the rodeo builder server"})
    status, body = _post(Api(can_save=True), "retell", {"chapters": CHAPTERS, "theme": "pirates", "engine": "ollama"})
    assert status == 200 and "pirates" in body["chapters"][0]["body"]
    assert _post(Api(can_save=True), "retell", {"chapters": CHAPTERS, "engine": "ollama"})[0] == 400


def test_engine_env_maps_a_stored_credential_to_the_engines_variables():
    assert storywork.engine_env("deepl", "deepl", {"auth_key": "K"}) == {"DEEPL_AUTH_KEY": "K"}
    assert storywork.engine_env("microsoft-translator", "azure-translator", {"key": "K", "region": ""}) == {
        "AZURE_TRANSLATOR_KEY": "K"}
    with pytest.raises(ValueError, match="needs a deepl credential"):
        storywork.engine_env("deepl", "gemini", {"api_key": "K"})
    with pytest.raises(ValueError, match="takes no stored credential"):
        storywork.engine_env("ollama", "deepl", {"auth_key": "K"})
    assert {t for t, _, _ in storywork.ENGINE_KEYS.values()} <= {t["id"] for t in vault.types()}


def test_api_takes_an_engines_key_from_a_stored_credential(fake_rmstory, monkeypatch):
    monkeypatch.setattr(labinabox_host, "CREDENTIALS_DIR", fake_rmstory.parent / "engine-credentials")
    monkeypatch.delenv("DEEPL_AUTH_KEY", raising=False)
    item = vault.add("my deepl", "deepl", {"auth_key": "K-STORED"}, "deepl password")
    scc = vault.add("my scc", "scc", {"regcode": "R"}, "scc password")
    live = Api(can_save=True, can_deploy=True)
    req = {"chapters": CHAPTERS, "to": "de", "engine": "deepl"}
    status, body = _post(live, "translate", req)
    assert status == 200 and body["log"].endswith("deepl key: no")
    status, body = _post(live, "translate", {**req, "credential": {"id": item["id"], "password": "deepl password"}})
    assert status == 200 and body["log"].endswith("deepl key: yes") and "K-STORED" not in json.dumps(body)
    assert _post(live, "translate", {**req, "credential": {"id": item["id"], "password": "nope"}}) == (
        403, {"error": "wrong password"})
    assert _post(live, "translate", {**req, "credential": {"id": scc["id"], "password": "scc password"}})[0] == 400
    assert _post(live, "translate", {**req, "credential": {"id": "0" * 16, "password": "x"}}) == (
        404, {"error": "unknown credential"})
    assert _post(live, "translate", {**req, "credential": {"id": "../x", "password": "x"}})[0] == 400
    status, body = _post(Api(can_save=True), "translate", {**req, "credential": {"id": item["id"], "password": "deepl password"}})
    assert status == 403 and "listens on this machine only" in body["error"]
