"""A lab's instructions apart from its infrastructure: the plan's content: block (rodeo/content.py)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from rodeo import content
from rodeo.commands import up_cmd
from rodeo.commands.content_cmd import content_cmd
from rodeo.config import ConfigError
from rodeo.story import story_dir

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.mark.parametrize("block,want", [
    ({"repo": "https://github.com/a/b", "format": "rmstory", "path": "/story/"},
     {"repo": "https://github.com/a/b", "ref": "", "path": "story", "format": "rmstory", "facts": {}, "replace": {}}),
    ({"repo": "https://h/a/b", "ref": "v1", "format": "instruqt", "path": "tracks/t", "facts": {"X": 1}},
     {"repo": "https://h/a/b", "ref": "v1", "path": "tracks/t", "format": "instruqt", "facts": {"X": "1"}, "replace": {}}),
])
def test_content_spec(block, want):
    assert content.content_spec({"content": block}) == want
    assert content.content_spec({}) is None


@pytest.mark.parametrize("block,match", [
    ("x", "mapping"),
    ({"repo": "http://h/a/b", "format": "rmstory"}, "https://"),
    ({"repo": "https://u:p@h/a/b", "format": "rmstory"}, "credentials"),
    ({"repo": "https://h/a/b", "format": "pdf"}, "format"),
    ({"repo": "https://h/a/b", "format": "rmstory", "ref": "-x"}, "ref"),
    ({"repo": "https://h/a/b", "format": "rmstory", "path": "a/../b"}, "path"),
])
def test_content_spec_refuses(block, match):
    with pytest.raises(ConfigError, match=match):
        content.content_spec({"content": block})


def _repo(root: Path, files: dict[str, str]) -> Path:
    repo = root / "src"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "c"], check=True)
    return repo


def _spec(repo: Path, fmt: str, path: str, **extra) -> dict:
    return {"repo": str(repo), "ref": "", "path": path, "format": fmt, "facts": {}, "replace": {}, **extra}


@needs_git
def test_apply_instruqt_renders_assignments_with_facts(tmp_path):
    repo = _repo(tmp_path, {"tracks/t/01-intro/assignment.md": '---\ntitle: Intro\n---\nOpen [[ Instruqt-Var key="URL" hostname="x" ]] on smlm.instruqt.io\n',
                            "tracks/t/02-next/assignment.md": "Next\n", "README.md": "x"})
    lab = tmp_path / "lab"
    spec = _spec(repo, "instruqt", "tracks/t", facts={"URL": "https://smlm.rodeo.lab"}, replace={"smlm.instruqt.io": "smlm.rodeo.lab"})
    commit = content.fetch(spec, lab)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert commit == head and yaml.safe_load((lab / content.LOCK).read_text())["commit"] == head
    assert not (lab / "content" / ".git").exists()
    files = content.render(spec, lab, {})
    assert [f.name for f in files] == ["01-intro.md", "02-next.md"]
    assert "Open https://smlm.rodeo.lab on smlm.rodeo.lab" in files[0].read_text()
    assert files[0].stat().st_mode & 0o777 == 0o600


@needs_git
def test_apply_markdown_and_a_second_apply_replaces_the_old_content(tmp_path):
    repo = _repo(tmp_path, {"docs/01-a.md": "# A on example.com\n", "docs/02-b.md": "# B\n"})
    lab = tmp_path / "lab"
    spec = _spec(repo, "markdown", "docs", replace={"example.com": "rodeo.lab"})
    content.fetch(spec, lab)
    (lab / "content" / "stale.txt").write_text("old")
    content.fetch(spec, lab)
    assert not (lab / "content" / "stale.txt").exists()
    files = content.render(spec, lab, {})
    assert [f.name for f in files] == ["01-a.md", "02-b.md"] and files[0].read_text() == "# A on rodeo.lab\n"
    with pytest.raises(ConfigError, match="no directory 'nope'"):
        content.fetch(_spec(repo, "markdown", "nope"), lab)
    with pytest.raises(RuntimeError, match="fetching"):
        content.fetch(_spec(tmp_path / "missing", "markdown", ""), lab)


@needs_git
def test_rmstory_content_is_the_story_of_a_lab_without_one(tmp_path, fake_cfg):
    repo = _repo(tmp_path, {"story/01-hello.md": '# Hello\n<span lang="en" id="hello.1">Saddle up</span>\n'})
    lab = tmp_path / "lab"
    cfg = {**fake_cfg, "config_dir": str(lab), "content": {"repo": "https://example.com/a/b", "format": "rmstory", "path": "story"}}
    spec = _spec(repo, "rmstory", "story")
    content.fetch(spec, lab)
    assert story_dir(cfg) == lab / "content" / "story"
    files = content.render(spec, lab, cfg)
    assert files == [lab / "guide" / "guide.md"] and "Saddle up" in files[0].read_text()
    (lab / "story").mkdir()
    assert story_dir(cfg) == lab / "story"


def test_apply_needs_a_content_block(tmp_path):
    with pytest.raises(ConfigError, match="no content: block"):
        content.apply({}, tmp_path)


def _lab_with_plan(tmp_path: Path, block: dict | None) -> Path:
    lab = tmp_path / "lab"
    lab.mkdir()
    plan = {"name": "ride", "type": "rancher", "deployment_target": "baremetal"}
    if block:
        plan["content"] = block
    (lab / "rodeo-plan.yaml").write_text(yaml.safe_dump(plan))
    return lab


def test_content_commands(tmp_path, monkeypatch):
    lab = _lab_with_plan(tmp_path, {"repo": "https://github.com/a/b", "format": "markdown", "path": "docs"})
    monkeypatch.chdir(lab)
    r = CliRunner().invoke(content_cmd, ["show"])
    assert r.exit_code == 0 and "format: markdown" in r.output and "not fetched yet" in r.output
    calls = []

    def fake_apply(cfg, where):
        calls.append(where)
        return {"commit": "abcdef1234567890", "files": [where / "guide" / "01-a.md"]}

    monkeypatch.setattr(content, "apply", fake_apply)
    r = CliRunner().invoke(content_cmd, ["apply"])
    assert r.exit_code == 0, r.output
    assert "Instructions from https://github.com/a/b @ abcdef123456" in r.output and calls == [lab]

    def failing(cfg, where):
        raise RuntimeError("fetching https://github.com/a/b: not found")
    monkeypatch.setattr(content, "apply", failing)
    r = CliRunner().invoke(content_cmd, ["apply"])
    assert r.exit_code == 1 and "not found" in r.output


def test_content_show_without_a_block(tmp_path, monkeypatch):
    monkeypatch.chdir(_lab_with_plan(tmp_path, None))
    r = CliRunner().invoke(content_cmd, ["show"])
    assert r.exit_code == 0 and "no content: block" in r.output


def test_up_applies_content_after_a_deploy_and_only_warns(monkeypatch, tmp_path, capsys):
    applied = []
    monkeypatch.setattr(content, "apply", lambda cfg, lab: applied.append(lab) or {"commit": "", "files": [lab / "guide" / "g.md"]})
    cfg = {"content": {"repo": "https://github.com/a/b", "format": "rmstory"}}
    up_cmd._apply_content(cfg, tmp_path)
    up_cmd._apply_content({}, tmp_path)
    assert applied == [tmp_path]

    def failing(cfg, lab):
        raise RuntimeError("network down")
    monkeypatch.setattr(content, "apply", failing)
    up_cmd._apply_content(cfg, tmp_path)
    up_cmd._apply_content({"content": {"repo": "http://bad", "format": "rmstory"}}, tmp_path)
