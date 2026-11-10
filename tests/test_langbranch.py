"""Language branches of a content repository (rodeo/langbranch.py): rodeo story check, the
branch rodeo content apply takes for the plan's story.language, the catalog warning."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from rodeo import catalog, content, langbranch
from rodeo.commands.story_cmd import story_cmd

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1", "HOME": "/nonexistent"}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=ENV).stdout


def _commit(repo: Path, path: str, text: str, message: str) -> None:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """main: plan; es, pt-br and dev from it; then main changes story/ (ignored) and the plan."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "rodeo-plan.yaml", "name: ride\n", "plan")
    for branch in ("es", "pt-br", "dev"):
        _git(repo, "branch", branch)
    _git(repo, "checkout", "-q", "es")
    _commit(repo, "story/strings/es/x.json", "{}", "es strings")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "story/01.md", "intro", "chapter")
    _commit(repo, ".github/workflows/ci.yml", "on: push", "ci")
    _commit(repo, "rodeo-plan.yaml", "name: ride\nresources: {}\n", "sizing")
    return repo


def _clone(origin: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True, capture_output=True, env=ENV)
    return dest


def test_behind_counts_only_commits_outside_the_instructions(origin, tmp_path):
    clone = _clone(origin, tmp_path / "clone")
    assert langbranch.default_branch(clone) == "origin/main"
    assert langbranch.language_branches(clone, "origin/main") == {"es": "origin/es", "pt-br": "origin/pt-br"}
    commits = langbranch.behind(clone, "origin/es", "origin/main")
    assert [c.split(" ", 1)[1] for c in commits] == ["sizing"]
    _git(origin, "checkout", "-q", "es")
    _git(origin, "merge", "-q", "--no-edit", "main")
    assert langbranch.behind(origin, "es", "main") == []
    with pytest.raises(RuntimeError):
        langbranch.behind(origin, "fr", "main")
    _git(origin, "checkout", "-q", "--orphan", "pages")
    _commit(origin, "index.html", "x", "pages")
    with pytest.raises(RuntimeError, match="pages shares no history with main"):
        langbranch.behind(origin, "pages", "main")


def test_remote_behind_reads_only_the_history(origin):
    assert [c.split(" ", 1)[1] for c in langbranch.remote_behind(str(origin), "es")] == ["sizing"]
    assert langbranch.remote_behind(str(origin), "fr") is None
    with pytest.raises(RuntimeError):
        langbranch.remote_behind(str(origin.parent / "nowhere"), "es")


def test_story_check_lists_what_each_branch_misses(origin, tmp_path):
    clone = _clone(origin, tmp_path / "clone")
    r = CliRunner().invoke(story_cmd, ["check", str(clone)])
    assert r.exit_code == 1
    assert "✗  es is 1 commit(s) behind origin/main:" in r.output and "sizing" in r.output
    assert "pt-br is 1 commit(s) behind" in r.output and "dev" not in r.output
    r = CliRunner().invoke(story_cmd, ["check", str(clone), "--language", "fr"])
    assert r.exit_code == 1 and "✗  fr:" in r.output
    for branch in ("es", "pt-br"):
        _git(origin, "checkout", "-q", branch)
        _git(origin, "merge", "-q", "--no-edit", "main")
    r = CliRunner().invoke(story_cmd, ["check", str(origin), "--source", "main"])
    assert r.exit_code == 0, r.output
    assert "✓  es is up to date with main" in r.output and "✓  pt-br is up to date with main" in r.output
    lone = tmp_path / "lone"
    lone.mkdir()
    _git(lone, "init", "-q", "-b", "main")
    _commit(lone, "a", "a", "a")
    r = CliRunner().invoke(story_cmd, ["check", str(lone)])
    assert r.exit_code == 0 and "No language branches" in r.output


@pytest.mark.parametrize("language,ref,want_ref,warning", [
    ("es", "", "es", "the es branch is 1 commit(s) behind the default branch"),
    ("fr", "", "", "has no fr branch: the instructions come from its default branch"),
    ("es", "es", "es", "the es branch is 1 commit(s) behind"),
    ("es", "v1", "v1", None),
    ("en", "", "", None),
    (None, "", "", None),
])
def test_content_takes_the_plans_language_branch(origin, language, ref, want_ref, warning):
    cfg = {"story": {"language": language}} if language else {}
    spec, warnings = content._language_branch({"repo": str(origin), "ref": ref}, cfg, subprocess.run)
    assert spec["ref"] == want_ref
    if warning:
        assert len(warnings) == 1 and warning in warnings[0]
    else:
        assert warnings == []


def test_content_apply_reports_the_branch_and_its_warning(tmp_path, monkeypatch):
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "rodeo-plan.yaml").write_text(yaml.safe_dump({
        "name": "ride", "story": {"language": "es"},
        "content": {"repo": "https://github.com/a/b", "format": "markdown"}}))
    monkeypatch.chdir(lab)
    monkeypatch.setattr(content, "apply", lambda cfg, where: {
        "commit": "abc", "ref": "es", "files": [], "checks": None, "warnings": [],
        "language": ["the es branch is 2 commit(s) behind the default branch of https://github.com/a/b: x"]})
    from rodeo.commands.content_cmd import content_cmd
    r = CliRunner().invoke(content_cmd, ["apply"])
    assert r.exit_code == 0, r.output
    assert "Instructions from https://github.com/a/b (es) @ abc" in r.output
    assert "the es branch is 2 commit(s) behind" in r.output


def test_catalog_warns_when_the_language_branch_is_behind(origin, tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "rodeo-plan.yaml").write_text(yaml.safe_dump({"name": "ride", "story": {"language": "es"}}))
    warning = catalog.language_warning(profile, str(origin), "es")
    assert warning and "the es branch is 1 commit(s) behind" in warning and "sizing" in warning
    assert catalog.language_warning(profile, str(origin), "") is None
    assert catalog.language_warning(profile, str(origin), "main") is None
    _git(origin, "checkout", "-q", "es")
    _git(origin, "merge", "-q", "--no-edit", "main")
    assert catalog.language_warning(profile, str(origin), "es") is None
    assert catalog.language_warning(tmp_path / "none", str(origin), "es") is None
