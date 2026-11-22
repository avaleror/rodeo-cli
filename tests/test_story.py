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
    from rodeo.config import trust_lab

    _lab(tmp_path, "story:\n  engine_env:\n    GEMINI_API_KEY: sekret\n")
    trust_lab(tmp_path)  # story.engine_env reaches rmstory only in a trusted lab
    cfg = load_config(tmp_path / "rodeo-plan.yaml", config_dir=tmp_path, secrets="keys")
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


# ---------- success screen (A2) ----------

def test_success_story_bundled_rancher(tmp_path):
    (tmp_path / "rodeo-plan.yaml").write_text("type: rancher\nname: demo\n")
    cfg = load_config(tmp_path / "rodeo-plan.yaml", config_dir=tmp_path)
    text = story_mod.render_success_story(cfg)
    assert "First things to try" in text
    assert "rodeo ssh rancher" in text
    assert "install an app from Charts" in text
    assert "<span" not in text  # stripped for terminal output


def test_success_story_bundled_suse_edge_renders_edge_table(tmp_path):
    (tmp_path / "rodeo-plan.yaml").write_text("type: suse-edge\nname: edge\n")
    cfg = load_config(tmp_path / "rodeo-plan.yaml", config_dir=tmp_path)
    text = story_mod.render_success_story(cfg)
    assert "Edge node reference" in text
    assert "rodeo ssh eib" in text
    assert "alien-geeko" in text
    assert "edge1" in text


def test_success_story_lab_override_wins(tmp_path):
    cfg = _lab(tmp_path)
    (tmp_path / "story" / "success.md").write_text(
        '<span lang="en" id="s.custom">Custom payoff at <span no>{{ rancher_url }}</span></span>\n'
    )
    text = story_mod.render_success_story(cfg)
    assert "Custom payoff at https://192.168.122.9:30002" in text


def test_success_story_none_for_unknown_type(tmp_path):
    (tmp_path / "rodeo-plan.yaml").write_text("name: x\n")
    cfg = {"type": "", "vms": {}, "network": {}}
    assert story_mod.render_success_story(cfg) is None


def test_success_story_translates_when_language_set(tmp_path, fake_rmstory):
    cfg = _lab(tmp_path, "story:\n  language: es\n")
    text = story_mod.render_success_story(cfg)
    assert "[es]" in text  # fake translator prefixes content
    (cmd,) = [c for c in fake_rmstory.calls if c[1] == "translate"]
    assert cmd[cmd.index("--to") + 1] == "es"


def test_success_story_degrades_to_english_without_rmstory(tmp_path, monkeypatch):
    monkeypatch.setattr(story_mod.shutil, "which", lambda name: None)
    cfg = _lab(tmp_path, "story:\n  language: es\n")
    text = story_mod.render_success_story(cfg)
    assert "First things to try" in text  # English source, no exception


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


def test_story_render_cli_gets_key_passwords_but_runs_nothing(tmp_path, monkeypatch):
    """story render loads with secrets="keys": ??key passwords reach the hand-out,
    a trusted ??cmd: does not run."""
    from click.testing import CliRunner

    from rodeo import config
    from rodeo.commands.story_cmd import story_cmd

    marker = tmp_path / "ran"
    spec = f"??cmd:touch {marker} && echo ok"
    _lab(tmp_path,
         "credentials:\n  rancher_admin_password: ??rancher_admin_password\n"
         f'registry:\n  token: "{spec}"\n')
    (tmp_path / "story" / "03-creds.md").write_text(
        "Password: <span no>{{ credentials.rancher_admin_password }}</span>\n"
    )
    secrets = tmp_path / ".rodeo" / "secrets.yaml"
    secrets.parent.mkdir(parents=True, exist_ok=True)
    secrets.write_text("rancher_admin_password: StoryPass123\n")
    config.trust_lab(tmp_path)

    result = CliRunner().invoke(
        story_cmd, ["render", "--config", str(tmp_path / "rodeo-plan.yaml")]
    )
    assert result.exit_code == 0, result.output
    assert "StoryPass123" in result.output
    assert not marker.exists()


def test_story_template_is_sandboxed(tmp_path):
    """SSTI: story text from a workshop repo must not reach Python internals."""
    cfg = _lab(tmp_path)
    (tmp_path / "story" / "04-evil.md").write_text(
        "{{ ''.__class__.__mro__[1].__subclasses__() }}\n"
    )
    with pytest.raises(ConfigError, match="sandbox"):
        render_story(cfg)


def test_story_template_range_is_capped(tmp_path):
    cfg = _lab(tmp_path)
    (tmp_path / "story" / "04-loop.md").write_text("{% for i in range(10**9) %}x{% endfor %}\n")
    with pytest.raises(ConfigError, match="range"):
        render_story(cfg)


def test_story_render_untrusted_lab_cannot_read_global_secrets(tmp_path):
    """Attack: a cloned lab asks for a global secret in credentials; story render
    (keys mode) must not put it into the hand-out."""
    from click.testing import CliRunner

    from rodeo.commands.story_cmd import story_cmd

    _lab(tmp_path, "credentials:\n  rancher_admin_password: ??aws_secret_access_key\n")
    (tmp_path / "story" / "03-creds.md").write_text(
        "Password: <span no>{{ credentials.rancher_admin_password }}</span>\n"
    )
    secrets = tmp_path / ".rodeo" / "secrets.yaml"
    secrets.parent.mkdir(parents=True, exist_ok=True)
    secrets.write_text("aws_secret_access_key: global-value-marker\n")
    result = CliRunner().invoke(
        story_cmd, ["render", "--config", str(tmp_path / "rodeo-plan.yaml")]
    )
    assert "global-value-marker" not in result.output


def test_untrusted_lab_story_engine_env_never_reaches_rmstory(tmp_path, fake_rmstory):
    """Attack: story.engine_env: {LD_PRELOAD: ...} in a cloned plan would run code in rmstory."""
    cfg = _lab(tmp_path, "story:\n  engine: evil\n  engine_env:\n    LD_PRELOAD: /tmp/evil.so\n")
    render_story(cfg, language="es")
    assert all("LD_PRELOAD" not in env for env in fake_rmstory.envs)
    assert all("evil" not in " ".join(c) for c in fake_rmstory.calls)


def test_story_render_with_translation_keeps_the_lab_trusted(tmp_path, fake_rmstory):
    """rmstory writes its translation store under story/strings: rodeo-written state."""
    from rodeo.config import is_lab_trusted, trust_lab

    _lab(tmp_path)
    trust_lab(tmp_path)
    cfg = load_config(tmp_path / "rodeo-plan.yaml", config_dir=tmp_path, secrets="keys")
    store = tmp_path / "story" / "strings"
    store.mkdir()
    (store / "es.json").write_text('{"intro.hello": "Hola"}')  # what rmstory leaves behind
    render_story(cfg, language="es")
    assert is_lab_trusted(tmp_path)


def test_translation_refuses_a_symlinked_strings_store(tmp_path, fake_rmstory):
    """rmstory writes story/strings, which the lab hash skips: never through a link."""
    import os

    cfg = _lab(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, tmp_path / "story" / "strings")
    with pytest.raises(ConfigError, match="symlink"):
        render_story(cfg, language="es")
    assert not [c for c in fake_rmstory.calls if c[1] == "translate"]
