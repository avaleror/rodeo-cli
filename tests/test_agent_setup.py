"""WP5: `rodeo agent-setup` writes the Claude Code kit; `rodeo doctor` reports it."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

from rodeo import agent_guard, agent_kit
from rodeo.cli import cli
from rodeo.service.doctor import agent_kit_report

PLAN = """\
name: kitlab
type: harvester
vms:
  harvester1:
    ip: 192.168.122.11
network:
  vip: 192.168.122.10
"""


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    d = tmp_path / "lab"
    d.mkdir()
    return d


def _settings(lab):
    return json.loads((lab / ".claude" / "settings.json").read_text())


def _setup(lab, *extra):
    return CliRunner().invoke(cli, ["agent-setup", "--dir", str(lab), *extra])


def test_writes_kit_into_an_empty_project(lab):
    result = _setup(lab)
    assert result.exit_code == 0, result.output
    s = _settings(lab)
    for rule in agent_kit.BASE_DENY:
        assert rule in s["permissions"]["deny"]
    [entry] = s["hooks"]["PreToolUse"]
    assert entry["matcher"] == "Bash"
    assert "agent_guard.py" in entry["hooks"][0]["command"]
    assert "mistake filter" in result.output
    assert "NOPASSWD sudo" in result.output


def test_rerun_is_idempotent(lab):
    assert _setup(lab).exit_code == 0
    first = (lab / ".claude" / "settings.json").read_text()
    result = _setup(lab)
    assert result.exit_code == 0
    assert "already up to date" in result.output
    assert (lab / ".claude" / "settings.json").read_text() == first


def test_existing_rules_and_hooks_survive(lab):
    (lab / ".claude").mkdir()
    user = {
        "model": "opus",
        "permissions": {
            "allow": ["Bash(make test)"],
            "deny": ["Read(./.env)", "Bash(sudo *)"],
            "ask": ["Bash(git push *)"],
        },
        "hooks": {
            "PreToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "lint.sh"}]}],
            "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "log.sh"}]}],
        },
        "env": {"FOO": "1"},
    }
    (lab / ".claude" / "settings.json").write_text(json.dumps(user))
    assert _setup(lab).exit_code == 0
    assert _setup(lab).exit_code == 0
    s = _settings(lab)
    assert s["model"] == "opus" and s["env"] == {"FOO": "1"}
    assert s["permissions"]["allow"] == ["Bash(make test)"]
    assert s["permissions"]["ask"] == ["Bash(git push *)"]
    deny = s["permissions"]["deny"]
    assert deny[:2] == ["Read(./.env)", "Bash(sudo *)"]
    assert deny.count("Bash(sudo *)") == 1
    pre = s["hooks"]["PreToolUse"]
    assert pre[0] == user["hooks"]["PreToolUse"][0]
    assert sum(agent_kit._is_ours(e) for e in pre) == 1
    assert s["hooks"]["PostToolUse"] == user["hooks"]["PostToolUse"]


def test_lab_hosts_get_ssh_denies_and_reach_the_hook(lab):
    (lab / "rodeo-plan.yaml").write_text(PLAN)
    hosts = agent_kit.lab_hosts(lab)
    assert {"harvester1", "192.168.122.11", "192.168.122.10"} <= set(hosts)
    assert _setup(lab).exit_code == 0
    s = _settings(lab)
    assert "Bash(ssh *192.168.122.11*)" in s["permissions"]["deny"]
    assert "--host 192.168.122.11" in s["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def test_hostile_host_names_are_not_put_in_the_hook(lab, monkeypatch):
    (lab / "rodeo-plan.yaml").write_text("name: x\n")
    cfg = {"vms": {"$(touch MARKER)": {"ip": "a;b"}, "-oProxyCommand=x": {}, "ok-host": {}}}
    monkeypatch.setattr("rodeo.config.load_config", lambda *a, **k: cfg)
    assert agent_kit.lab_hosts(lab) == ["ok-host"]


@pytest.mark.parametrize("content, word", [
    ("not json {", "not valid JSON"),
    ("[1, 2]", "JSON object"),
    ('{"permissions": []}', "settings.permissions"),
    ('{"permissions": {"deny": "Bash(x)"}}', "permissions.deny"),
    ('{"hooks": {"PreToolUse": {}}}', "hooks.PreToolUse"),
])
def test_odd_existing_json_is_refused_not_overwritten(lab, content, word):
    (lab / ".claude").mkdir()
    path = lab / ".claude" / "settings.json"
    path.write_text(content)
    result = _setup(lab)
    assert result.exit_code != 0
    assert word in result.output
    assert path.read_text() == content


def test_empty_settings_file_is_treated_as_empty(lab):
    (lab / ".claude").mkdir()
    (lab / ".claude" / "settings.json").write_text("")
    assert _setup(lab).exit_code == 0
    assert "Bash(rodeo *)" in _settings(lab)["permissions"]["deny"]


def test_symlinked_settings_file_is_refused(lab, tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    (lab / ".claude").mkdir()
    (lab / ".claude" / "settings.json").symlink_to(target)
    result = _setup(lab)
    assert result.exit_code != 0
    assert "symlink" in result.output
    assert target.read_text() == "{}"


def test_symlinked_claude_dir_is_refused(lab, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (lab / ".claude").symlink_to(elsewhere)
    result = _setup(lab)
    assert result.exit_code != 0
    assert not (elsewhere / "settings.json").exists()


def test_check_and_doctor_flag_absence_then_presence(lab, monkeypatch):
    monkeypatch.chdir(lab)
    assert _setup(lab, "--check").exit_code == 1
    assert agent_kit_report(lab)["claude_code"] is False
    out = CliRunner().invoke(cli, ["doctor"]).output
    assert "no Claude Code kit" in out
    assert _setup(lab).exit_code == 0
    assert _setup(lab, "--check").exit_code == 0
    assert agent_kit_report(lab)["claude_code"] is True
    assert "Claude Code kit in" in CliRunner().invoke(cli, ["doctor"]).output


def test_doctor_json_carries_the_kit(lab, monkeypatch):
    monkeypatch.chdir(lab)
    report = json.loads(CliRunner().invoke(cli, ["doctor", "--output", "json"]).output)
    assert report["agent_kit"]["claude_code"] is False


def test_doctor_names_clients_with_no_kit(lab, monkeypatch):
    (lab / ".gemini").mkdir()
    (lab / ".gemini" / "settings.json").write_text("{}")
    assert agent_kit_report(lab)["clients_without_kit"] == ["Gemini CLI"]


def test_kit_does_not_untrust_the_lab(lab):
    from rodeo.config import _unhashed

    assert _unhashed(".claude/settings.json")


@pytest.mark.parametrize("command", [
    "rodeo up",
    "rodeo",
    "/usr/local/bin/rodeo status",
    "cd lab && rodeo deploy --no-tui",
    "FOO=1 rodeo plan",
    "env -i rodeo plan",
    "nohup sudo virsh list &",
    "sudo -n true",
    "virsh destroy harvester1",
    "bash -c 'rodeo up --yes'",
    "echo \"$(rodeo status)\"",
    "echo `rodeo status`",
    "ls | xargs echo; rodeo destroy --yes",
    "echo up | xargs -n1 rodeo",
    "ssh root@192.168.122.11 uptime",
    "scp file harvester1:/tmp/",
    "rodeo 'unbalanced",
])
def test_guard_blocks(command):
    assert agent_guard.check(command, frozenset({"192.168.122.11", "harvester1"})) is not None


@pytest.mark.parametrize("command", [
    "cat rodeo-plan.yaml",
    "grep -r rodeo .",
    "ls ~/rodeo-labs",
    "git log --grep sudo",
    "pytest -k virsh",
    "ssh other.example.com uptime",
    "python -m pytest tests",
])
def test_guard_allows(command):
    assert agent_guard.check(command, frozenset({"192.168.122.11"})) is None


def test_guard_hook_protocol(monkeypatch, capsys):
    event = {"tool_name": "Bash", "tool_input": {"command": "rodeo up"}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    assert agent_guard.main([]) == 2
    assert "MCP tools" in capsys.readouterr().err
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert agent_guard.main([]) == 0


def test_written_hook_command_runs(lab):
    """The exact command stored in the settings blocks rodeo through a real shell."""
    assert _setup(lab).exit_code == 0
    command = _settings(lab)["hooks"]["PreToolUse"][-1]["hooks"][0]["command"]
    # A planted json.py in the lab (Claude Code's cwd) must never be imported.
    (lab / "json.py").write_text("import pathlib; pathlib.Path('MARKER').touch()\n")
    env = dict(os.environ)
    event = json.dumps({"tool_name": "Bash", "tool_input": {"command": "sudo rodeo up"}})
    proc = subprocess.run(command, shell=True, input=event, text=True, cwd=lab,
                          capture_output=True, env=env, check=False)
    assert proc.returncode == 2, proc.stderr
    assert not (lab / "MARKER").exists()
    ok = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    proc = subprocess.run(command, shell=True, input=ok, text=True, cwd=lab,
                          capture_output=True, env=env, check=False)
    assert proc.returncode == 0, proc.stderr


def test_doctor_flags_a_hook_that_points_at_a_moved_rodeo(lab):
    assert _setup(lab).exit_code == 0
    path = lab / ".claude" / "settings.json"
    s = json.loads(path.read_text())
    s["hooks"]["PreToolUse"][-1]["hooks"][0]["command"] = "/gone/python -I /gone/agent_guard.py"
    path.write_text(json.dumps(s))
    report = agent_kit_report(lab)
    assert report["claude_code"] is False
    assert "re-run rodeo agent-setup" in report["problem"]
    assert _setup(lab).exit_code == 0  # re-running repairs it in place
    assert agent_kit_report(lab)["claude_code"] is True
    assert sum(agent_kit._is_ours(e) for e in _settings(lab)["hooks"]["PreToolUse"]) == 1
