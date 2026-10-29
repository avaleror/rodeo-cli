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
def test_fetch_never_follows_a_symlink_out_of_the_repository(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    repo = _repo(tmp_path, {"docs/01-a.md": "# A\n"})
    (repo / "docs" / "02-leak.md").symlink_to(secret)
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "link"], check=True)
    lab = tmp_path / "lab"
    spec = _spec(repo, "markdown", "docs")
    content.fetch(spec, lab)
    assert not (lab / "content" / "docs" / "02-leak.md").is_symlink()
    assert all("TOP-SECRET" not in f.read_text() for f in content.render(spec, lab, {}))


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
        return {"commit": "abcdef1234567890", "files": [where / "guide" / "01-a.md"], "checks": None, "warnings": []}

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
    monkeypatch.setattr(content, "apply", lambda cfg, lab: applied.append(lab) or {"commit": "", "files": [lab / "guide" / "g.md"],
                                                                          "checks": None, "warnings": []})
    cfg = {"content": {"repo": "https://github.com/a/b", "format": "rmstory"}}
    up_cmd._apply_content(cfg, tmp_path)
    up_cmd._apply_content({}, tmp_path)
    assert applied == [tmp_path]

    def failing(cfg, lab):
        raise RuntimeError("network down")
    monkeypatch.setattr(content, "apply", failing)
    up_cmd._apply_content(cfg, tmp_path)
    up_cmd._apply_content({"content": {"repo": "http://bad", "format": "rmstory"}}, tmp_path)


# ── checks/ and custom/scripts/ of the content ─────────────────────────────

def _fetched(lab: Path, files: dict[str, tuple[str, int]]) -> Path:
    """A lab whose content was fetched with *files* ({path: (text, mode)})."""
    for rel, (text, mode) in files.items():
        path = lab / content.CHECKOUT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)
    return lab


def test_install_checks_into_opt_rodeo(tmp_path, monkeypatch):
    monkeypatch.setattr(content, "CHECKS_ROOT", tmp_path / "opt")
    monkeypatch.setattr(content.os, "geteuid", lambda: 0)
    lab = _fetched(tmp_path / "lab", {"checks/check-1.sh": ("#!/bin/sh\n", 0o700), "checks/README.md": ("r", 0o600),
                                      "checks/lib/x.sh": ("x", 0o600)})
    (lab / "content" / "checks" / "link").symlink_to("/etc/passwd")
    dest = content.install_checks({"name": "ride"}, lab)
    assert dest == tmp_path / "opt" / "ride" / "checks"
    assert sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*")) == ["README.md", "check-1.sh", "lib", "lib/x.sh"]
    assert (dest / "check-1.sh").stat().st_mode & 0o777 == 0o755
    assert (dest / "README.md").stat().st_mode & 0o777 == 0o644
    assert (dest / "lib").stat().st_mode & 0o777 == 0o755 and dest.stat().st_mode & 0o777 == 0o755
    (lab / "content" / "checks" / "README.md").unlink()
    content.install_checks({"name": "ride"}, lab)
    assert not (dest / "README.md").exists()
    assert content.install_checks({"name": "ride"}, tmp_path / "empty") is None
    with pytest.raises(ConfigError, match="can't name a directory"):
        content.install_checks({"name": "../x"}, lab)
    monkeypatch.setattr(content.os, "geteuid", lambda: 1000)
    with pytest.raises(PermissionError, match="needs root"):
        content.install_checks({"name": "ride"}, lab)


def test_apply_reports_checks_it_could_not_install(tmp_path, monkeypatch):
    lab = _fetched(tmp_path / "lab", {"checks/c.sh": ("x", 0o755)})
    monkeypatch.setattr(content, "fetch", lambda spec, where, run: "abc")
    monkeypatch.setattr(content, "render", lambda spec, where, cfg: [where / "guide" / "g.md"])
    monkeypatch.setattr(content.os, "geteuid", lambda: 1000)
    cfg = {"name": "ride", "content": {"repo": "https://github.com/a/b", "format": "markdown"}}
    result = content.apply(cfg, lab)
    assert result["checks"] is None and "needs root" in result["warnings"][0]


def test_setup_scripts_run_in_order_with_rodeos_environment(tmp_path, monkeypatch):
    out = tmp_path / "ran.txt"
    script = "#!/bin/sh\necho \"$(basename \"$0\") $PWD $RODEO_PLAN_NAME $RODEO_CONTENT_DIR\" >> " + str(out) + "\n"
    lab = _fetched(tmp_path / "lab", {"custom/scripts/20-b.sh": (script + "echo failing; exit 3\n", 0o755),
                                      "custom/scripts/10-a.sh": (script + "echo hello\n", 0o755),
                                      "custom/scripts/README.md": ("not run", 0o644)})
    results = list(content.run_setup({"name": "ride", "config_dir": str(lab), "ansible": {}}, lab))
    assert [(name, code) for name, code, _ in results] == [("10-a.sh", 0), ("20-b.sh", 3)]
    assert results[0][2] == ["hello"] and results[1][2] == ["failing"]
    scripts = lab / "content" / "custom" / "scripts"
    assert out.read_text().splitlines() == [f"10-a.sh {scripts} ride {lab / 'content'}", f"20-b.sh {scripts} ride {lab / 'content'}"]
    assert list(content.run_setup({"name": "ride"}, tmp_path / "none")) == []


def test_setup_is_left_out_of_rodeo_up_where_custom_scripts_are():
    assert content.setup_guarded({"type": "suse-virt", "deployment_target": "instruqt"})
    assert not content.setup_guarded({"type": "suse-virt", "deployment_target": "baremetal"})
    assert not content.setup_guarded({"type": "lab-in-a-box", "deployment_target": "instruqt"})


def test_content_apply_runs_setup_and_fails_on_a_failing_script(tmp_path, monkeypatch):
    lab = _lab_with_plan(tmp_path, {"repo": "https://github.com/a/b", "format": "markdown", "path": "docs"})
    monkeypatch.chdir(lab)
    monkeypatch.setattr(content, "apply", lambda cfg, where: {"commit": "abc", "files": [], "checks": Path("/opt/rodeo/ride/checks"),
                                                               "warnings": []})
    monkeypatch.setattr(content, "run_setup", lambda cfg, where: iter([("10-a.sh", 0, ["hi"]), ("20-b.sh", 2, [])]))
    r = CliRunner().invoke(content_cmd, ["apply"])
    assert r.exit_code == 1
    assert "Checks installed in /opt/rodeo/ride/checks" in r.output and "10-a.sh" in r.output and "20-b.sh exited 2" in r.output
    monkeypatch.setattr(content, "run_setup", lambda cfg, where: iter([("10-a.sh", 0, [])]))
    assert CliRunner().invoke(content_cmd, ["apply"]).exit_code == 0


def test_up_runs_setup_unless_guarded(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(content, "apply", lambda cfg, lab: {"commit": "", "files": [], "checks": None, "warnings": []})
    ran = []
    monkeypatch.setattr(content, "run_setup", lambda cfg, lab: ran.append(cfg["deployment_target"]) or iter([("s.sh", 1, [])]))
    monkeypatch.setattr(content, "setup_scripts", lambda lab: [lab / "s.sh"])
    block = {"repo": "https://github.com/a/b", "format": "rmstory"}
    up_cmd._apply_content({"type": "suse-virt", "deployment_target": "baremetal", "content": block}, tmp_path)
    up_cmd._apply_content({"type": "suse-virt", "deployment_target": "instruqt", "content": block}, tmp_path)
    assert ran == ["baremetal"]
