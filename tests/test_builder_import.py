"""Rodeo Builder: importing a workshop's chapters from a git URL (rodeo/builder/imports.py)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rodeo.builder import imports
from rodeo.builder.api import Api


@pytest.mark.parametrize("url,want", [
    ("https://github.com/acme/labs", {"repo": "https://github.com/acme/labs", "ref": "", "path": ""}),
    ("https://github.com/acme/labs.git", {"repo": "https://github.com/acme/labs", "ref": "", "path": ""}),
    ("https://github.com/acme/labs/tree/v2/docs/exercises/",
     {"repo": "https://github.com/acme/labs", "ref": "v2", "path": "docs/exercises"}),
    ("https://gitlab.example.com/team/ws/blob/main/tracks/t1",
     {"repo": "https://gitlab.example.com/team/ws", "ref": "main", "path": "tracks/t1"}),
])
def test_parse_source_url(url, want):
    assert imports.parse_source_url(url) == want


@pytest.mark.parametrize("url", ["http://github.com/a/b", "file:///etc", "ssh://git@github.com/a/b",
                                 "https://user:pw@github.com/a/b", "https://github.com/a/b?x=1",
                                 "https://github.com/a", "https://github.com/a/b/tree/main/../x",
                                 "https://github.com/a/b/tree/-bad/x", "", "-oProxyCommand=x"])
def test_parse_source_url_refuses(url):
    with pytest.raises(ValueError):
        imports.parse_source_url(url)


def test_fetch_commands_are_shallow_sparse_and_end_options():
    clone, sparse = imports.fetch_commands({"repo": "https://h/a/b", "ref": "v1", "path": "docs/ex"}, Path("/t/x"))
    assert clone[:7] == ["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse"]
    assert clone[7:] == ["-c", "core.symlinks=false", "--branch", "v1", "--", "https://h/a/b", "/t/x"]
    assert "/docs/ex/*/assignment.md" in sparse and "/docs/ex/story/*.md" in sparse
    clone, sparse = imports.fetch_commands({"repo": "https://h/a/b", "ref": "", "path": ""}, Path("/t/x"))
    assert "--branch" not in clone and "/*.md" in sparse and "/story/chapters.yaml" in sparse


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def test_read_group_detects_each_format(tmp_path):
    src = {"repo": "https://github.com/acme/labs", "ref": "main", "path": "ws"}
    story = _write(tmp_path / "a", {
        "ws/story/01-hello.md": '# Hello\n<span lang="en" id="hello.1" hist="short">Hi</span>\n',
        "ws/story/chapters.yaml": "title: Acme ride\nchapters:\n  - file: 01-hello.md\n    mins: 7\n    needs: [rancher]\n",
        "ws/story/stories/short.yaml": "- hello.1\n"})
    g = imports.read_group(src, story)
    assert (g["format"], g["title"], g["id"]) == ("rmstory", "Acme ride", "url-acme-labs-ws")
    assert g["chapters"][0]["mins"] == 7 and g["chapters"][0]["needs"] == ["rancher"]
    assert g["variants"] == {"short": ["hello.1"]} and g["imported"] and g["engine"] == ""
    track = _write(tmp_path / "b", {"ws/01-intro/assignment.md": "---\ntitle: Intro\n---\nText\n",
                                    "ws/01-intro/check-workstation": "#!/bin/sh\nexit 0\n"})
    g = imports.read_group(src, track)
    assert g["format"] == "instruqt" and g["chapters"][0]["title"] == "Intro" and g["chapters"][0]["check"]
    md = _write(tmp_path / "c", {"ws/02-go.md": "# Go\n\n**Time:** 25 min\n"})
    g = imports.read_group(src, md)
    assert g["format"] == "markdown" and (g["chapters"][0]["id"], g["chapters"][0]["mins"]) == ("go", 25)
    with pytest.raises(ValueError, match="no chapters found"):
        imports.read_group(src, _write(tmp_path / "d", {"ws/notes.txt": "x"}))


def test_import_source_clones_and_reads(tmp_path):
    fixture = _write(tmp_path / "fixture", {"docs/01-a.md": "# A\n"})
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        assert kw["env"]["GIT_TERMINAL_PROMPT"] == "0"
        if cmd[1] == "clone":
            shutil.copytree(fixture, cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    g = imports.import_source("https://github.com/acme/labs/tree/main/docs", run=run)
    assert [c["title"] for c in g["chapters"]] == ["A"] and len(calls) == 2
    assert g["source"] == "https://github.com/acme/labs/tree/main/docs"

    def failing(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 128, "", "fatal: repository not found")

    with pytest.raises(RuntimeError, match="repository not found"):
        imports.import_source("https://github.com/acme/nope", run=failing)


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_import_never_follows_a_symlink_out_of_the_clone(tmp_path):
    secret = _write(tmp_path / "host", {"secret.txt": "TOKEN=hunter2\n"}) / "secret.txt"
    repo = _write(tmp_path / "repo", {"docs/01-a.md": "# A\n"})
    (repo / "docs" / "02-leak.md").symlink_to(secret)
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-qm", "x"], check=True)
    src = {"repo": repo.as_uri(), "ref": "", "path": "docs"}
    for cmd in imports.fetch_commands(src, tmp_path / "clone"):
        subprocess.run(cmd, check=True, capture_output=True)
    bodies = [c["body"] for c in imports.read_group(src, tmp_path / "clone")["chapters"]]
    assert not any("hunter2" in b for b in bodies)


def test_api_import_is_live_only(monkeypatch, tmp_path):
    body = json.dumps({"url": "https://github.com/acme/labs"}).encode()
    assert Api().dispatch("import", "POST", body=body) == (403, {"error": "importing a workshop needs the rodeo builder server"})
    monkeypatch.setattr(imports, "import_source", lambda url: {"id": "url-x", "url": url})
    assert Api(can_save=True).dispatch("import", "POST", body=body) == (200, {"workshop": {"id": "url-x", "url": "https://github.com/acme/labs"}})
    monkeypatch.undo()
    assert Api(can_save=True).dispatch("import", "POST", body=b'{"url": "http://x/a/b"}')[0] == 400

    def broken(url):
        raise RuntimeError("fetching: timeout")
    monkeypatch.setattr(imports, "import_source", broken)
    assert Api(can_save=True).dispatch("import", "POST", body=body) == (502, {"error": "fetching: timeout"})
