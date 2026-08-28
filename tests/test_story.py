"""Story rendering: languages + variants via the rmstory CLI, facts via Jinja.

The default path (source language, no variant) never invokes rmstory at all,
so labs without translations work with nothing extra installed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from rodeo import story as story_mod
from rodeo.cli import cli
from rodeo.config import ConfigError, load_config
from rodeo.story import render_story, story_facts


def _lab(tmp_path, plan_extra: str = "") -> dict:
    (tmp_path / "rodeo-plan.yaml").write_text("type: rancher\nname: demo\n" + plan_extra)
    story = tmp_path / "story"
    story.mkdir()
    (story / "01-intro.md").write_text(
        '# Welcome\n<span lang="en" id="intro.hello">Hello, wrangler.</span>\n'
        "Rancher: <span no>{{ rancher_url }}</span>\n"
    )
    (story / "02-tasks.md").write_text(
        '<span lang="en" id="tasks.first" hist="villain-arc">Find the villain.</span>\n'
    )
    (story / "stories").mkdir()
    (story / "stories" / "villain-arc.yaml").write_text("- tasks.first\n")
    return load_config(tmp_path / "rodeo-plan.yaml", config_dir=tmp_path)


class _FakeRmstory:
    """Stands in for the rmstory CLI: records calls, writes plausible outputs."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.envs: list[dict] = []

    def __call__(self, cmd, capture_output=True, text=True, env=None):
        self.calls.append(list(cmd))
        self.envs.append(dict(env or {}))
        verb = cmd[1]
        if verb == "translate":
            out_dir = Path(cmd[cmd.index("--out") + 1])
            lang = cmd[cmd.index("--to") + 1]
            for f in cmd[2:]:
                if f.endswith(".md"):
                    src = Path(f)
                    (out_dir / src.name).write_text(f"[{lang}] " + src.read_text())
        elif verb == "story":
            out_file = Path(cmd[cmd.index("--out") + 1])
            out_file.write_text("Find the villain.\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.fixture
def fake_rmstory(monkeypatch):
    fake = _FakeRmstory()
    monkeypatch.setattr(story_mod.shutil, "which", lambda name: "/usr/bin/rmstory")
    monkeypatch.setattr(story_mod.subprocess, "run", fake)
    return fake


# ---------- facts ----------

def test_story_facts_for_rancher_profile(tmp_path):
    cfg = _lab(tmp_path)
    facts = story_facts(cfg)
    assert facts["rancher_url"] == "https://192.168.122.9:30002"
    assert facts["vms"]["rancher"]["ip"] == "192.168.122.9"
    assert "rancher" in facts["vm_names"]
    assert facts["dns_domain"] == "rodeo.lab"


# ---------- default path: no rmstory needed ----------

def test_render_source_language_needs_no_rmstory(tmp_path, monkeypatch):
    monkeypatch.setattr(story_mod.shutil, "which", lambda name: None)
    cfg = _lab(tmp_path)
    text = render_story(cfg)
    assert "Hello, wrangler." in text
    assert "Find the villain." in text
    assert "https://192.168.122.9:30002" in text
    assert "{{" not in text


def test_unknown_fact_fails_closed(tmp_path):
    cfg = _lab(tmp_path)
    (tmp_path / "story" / "03-bad.md").write_text("<span no>{{ nope }}</span>\n")
    with pytest.raises(ConfigError, match="unknown deployment fact.*nope"):
        render_story(cfg)


def test_missing_story_dir_says_how_to_start(tmp_path):
    (tmp_path / "rodeo-plan.yaml").write_text("type: rancher\nname: demo\n")
    cfg = load_config(tmp_path / "rodeo-plan.yaml", config_dir=tmp_path)
    with pytest.raises(ConfigError, match="story/ directory"):
        render_story(cfg)


# ---------- translation + assembly via the rmstory CLI ----------

def test_translate_invokes_rmstory_with_store_env(tmp_path, fake_rmstory):
    cfg = _lab(tmp_path, "story:\n  engine_env:\n    GEMINI_API_KEY: sekret\n")
    text = render_story(cfg, language="es")
    assert "[es]" in text and "Hello, wrangler." in text
    (cmd,) = [c for c in fake_rmstory.calls if c[1] == "translate"]
    assert cmd[cmd.index("--to") + 1] == "es"
    env = fake_rmstory.envs[0]
    assert env["RMSTORY_STORIES_PATH"] == str(tmp_path / "story" / "stories")
    assert env["RMSTORY_STRINGS_PATH"] == str(tmp_path / "story" / "strings")
    assert env["GEMINI_API_KEY"] == "sekret"


def test_engine_flag_is_passed_through(tmp_path, fake_rmstory):
    render_story(_lab(tmp_path), language="fr", engine="ollama")
    (cmd,) = [c for c in fake_rmstory.calls if c[1] == "translate"]
    assert cmd[cmd.index("--engine") + 1] == "ollama"


def test_story_variant_assembles_via_rmstory(tmp_path, fake_rmstory):
    text = render_story(_lab(tmp_path), story_id="villain-arc")
    (cmd,) = [c for c in fake_rmstory.calls if c[1] == "story"]
    assert cmd[cmd.index("--story") + 1] == "villain-arc"
    assert text.strip() == "Find the villain."


def test_plan_story_block_provides_defaults(tmp_path, fake_rmstory):
    cfg = _lab(tmp_path, "story:\n  language: es\n  id: villain-arc\n")
    render_story(cfg)
    verbs = [c[1] for c in fake_rmstory.calls]
    assert verbs == ["translate", "story"]


def test_missing_rmstory_binary_gives_install_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(story_mod.shutil, "which", lambda name: None)
    with pytest.raises(ConfigError, match="install-deps --story"):
        render_story(_lab(tmp_path), language="es")


def test_bundled_rancher_example_story_renders(tmp_path):
    from rodeo.labseed import seed_lab

    lab = seed_lab("rancher", tmp_path / "lab")
    cfg = load_config(lab / "rodeo-plan.yaml", config_dir=lab)
    text = render_story(cfg)
    assert "Saddle up" in text
    assert "https://192.168.122.9:30002" in text


# ---------- CLI ----------

def test_story_render_cli_stdout(tmp_path, monkeypatch):
    _lab(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["story", "render"])
    assert result.exit_code == 0, result.output
    assert "Hello, wrangler." in result.stdout
    assert "https://192.168.122.9:30002" in result.stdout


def test_story_render_cli_writes_file(tmp_path, monkeypatch):
    _lab(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["story", "render", "-o", "handout.md"])
    assert result.exit_code == 0, result.output
    assert "Hello, wrangler." in (tmp_path / "handout.md").read_text()
