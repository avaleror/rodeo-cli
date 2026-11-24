"""rodeo up --target aws --detach: the host records its own run, the laptop returns.

The remote scripts run for real under bash with a fake ``rodeo`` on PATH, as
in test_aws_up.py.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rodeo.install_source import bootstrap_fragment
from rodeo.mcp import tools as mcp_tools
from rodeo.providers import remote_up as mod
from rodeo.providers.base import ProvisionedHost
from rodeo.providers.remote_up import (
    _DEFAULT_INSTALL,
    _status_script,
    AlreadyRunning,
    remote_up_script,
)

linux_only = pytest.mark.skipif(sys.platform != "linux", reason="/proc and GNU tools, as on the host")


def _logs(home: Path) -> Path:
    d = home / ".rodeo" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _env(tmp_path: Path, body: str) -> dict:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    fake = bindir / "rodeo"
    fake.write_text(f"#!/bin/bash\n[ \"$1\" = trust ] && exit 0\n{body}\n")
    fake.chmod(0o755)
    return {**os.environ, "HOME": str(tmp_path), "PATH": f"{bindir}:{os.environ['PATH']}"}


def _run(tmp_path: Path, body: str, *, detach: bool) -> subprocess.CompletedProcess:
    script = remote_up_script(lab_dir=str(tmp_path / "lab"), profile="test", detach=detach)
    script = script.replace(bootstrap_fragment(install_url=_DEFAULT_INSTALL, ref=None), "true")
    return subprocess.run(["bash", "-c", script], env=_env(tmp_path, body),
                          capture_output=True, text=True, timeout=30)


def _status(tmp_path: Path, lines: int = 5) -> dict:
    proc = subprocess.run(["bash", "-c", _status_script(), "status", str(lines)],
                          env={**os.environ, "HOME": str(tmp_path)},
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    info = mod._marker_fields(proc.stdout, "AWS_UP_STATUS")
    info["tail"] = proc.stdout.split("AWS_UP_LOG\n", 1)[1]
    return info


def _wait_for(path: Path, timeout: float = 15.0) -> None:
    end = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < end, f"{path} never appeared"
        time.sleep(0.05)


# ---------- the script builder ----------

def test_detach_does_not_follow_the_log_and_the_default_still_does():
    followed = remote_up_script(lab_dir="/root/lab", profile="test")
    detached = remote_up_script(lab_dir="/root/lab", profile="test", detach=True)
    assert 'tail -c "+$off" --pid="$rp" -f "$log"' in followed
    assert "AWS_UP_EXIT:$ec" in followed
    assert "tail -c" not in detached and "--pid" not in detached
    assert "AWS_UP_DETACHED" in detached
    # Today's redirections stay: nothing of the job holds the SSH channel.
    assert '</dev/null >>"$log" 2>&1 & rp=$!' in detached
    assert '</dev/null >>"$HOME/.rodeo/logs/aws-up.log" 2>&1' in detached


def test_the_followed_script_is_today_s_script_apart_from_the_guard():
    """Human `rodeo up --target aws` runs the same remote command as before:
    only the already-running guard (first field of the pid file) and the
    removal of a stale exit file are new."""
    script = remote_up_script(lab_dir="/root/lab", profile="test")
    assert ("rodeo up --yes --no-tmux --profile test --dir /root/lab --target aws "
            '</dev/null >>"$log" 2>&1 & rp=$!; echo "$rp" >"$pidf"; '
            'tail -c "+$off" --pid="$rp" -f "$log"; '
            'wait "$rp"; ec=$?; rm -f "$pidf"; set -e; echo AWS_UP_EXIT:$ec; exit "$ec"') in script
    assert "AWS_UP_RUNNING" not in script and "AWS_UP_DETACHED" not in script


def test_the_detached_job_is_built_with_shlex_quote_not_nested_quotes():
    import shlex

    lab = "/root/it's a lab"
    script = remote_up_script(lab_dir=lab, profile="te'st", detach=True)
    words = shlex.split(script)
    job = words[words.index("-c", words.index("sh")) + 1]
    assert f"--dir {shlex.quote(lab)}" in job
    assert "--profile " + shlex.quote("te'st") in job


def test_run_remote_up_runs_the_same_argv_for_a_human_deploy(monkeypatch):
    seen = []

    def fake_run_remote(inv, host, argv, **kw):
        seen.append(argv)
        return type("R", (), {"ok": True, "rc": 0, "stdout": "AWS_UP_EXIT:0\n", "stderr": ""})()

    monkeypatch.setattr(mod, "run_remote", fake_run_remote)
    monkeypatch.setattr(mod, "resolve_ssh_identity", lambda **kw: "/tmp/key")
    assert mod.run_remote_up({"name": "lab", "provider": {"type": "aws"}}, _host()) is None
    argv = seen[0]
    assert argv[:4] == ["sudo", "-n", "bash", "-lc"]
    assert 'tail -c "+$off" --pid="$rp" -f "$log"' in argv[4]
    assert "AWS_UP_DETACHED" not in argv[4]


# ---------- the detached run on the host ----------

@linux_only
def test_detach_returns_while_rodeo_runs_then_records_the_exit_code(tmp_path):
    logs = _logs(tmp_path)
    t0 = time.monotonic()
    proc = _run(tmp_path, "echo deploying; sleep 3; echo done; exit 3", detach=True)
    assert proc.returncode == 0, proc.stderr
    assert time.monotonic() - t0 < 3
    info = mod._marker_fields(proc.stdout, "AWS_UP_DETACHED")
    pid = info["pid"]
    assert isinstance(pid, int) and info["start"].isdigit()
    assert info["log"] == str(logs / "aws-up.log")
    # The job wrote its own record: pid and the start time of that very process.
    recorded_pid, recorded_start = (logs / "aws-up.pid").read_text().split()
    assert int(recorded_pid) == pid and recorded_start == info["start"]
    stat = Path(f"/proc/{pid}/stat").read_text()
    assert stat.rsplit(") ", 1)[1].split()[19] == recorded_start
    running = _status(tmp_path)
    assert running["state"] == "running" and running["pid"] == pid

    _wait_for(logs / "aws-up.exit")
    assert (logs / "aws-up.exit").read_text().strip() == "3"
    end = time.monotonic() + 5
    while (logs / "aws-up.pid").exists() and time.monotonic() < end:
        time.sleep(0.05)
    assert not (logs / "aws-up.pid").exists()
    done = _status(tmp_path)
    assert done["state"] == "exited" and done["exit"] == "3"
    assert "done" in done["tail"]


@linux_only
def test_an_instant_failure_is_still_recorded(tmp_path):
    logs = _logs(tmp_path)
    proc = _run(tmp_path, "echo boom; exit 4", detach=True)
    assert proc.returncode == 0
    assert "AWS_UP_DETACHED pid=" in proc.stdout
    _wait_for(logs / "aws-up.exit")
    assert (logs / "aws-up.exit").read_text().strip() == "4"
    status = _status(tmp_path)
    assert status["state"] == "exited" and status["exit"] == "4"


@linux_only
def test_a_stale_exit_file_is_removed_before_a_new_run(tmp_path):
    logs = _logs(tmp_path)
    (logs / "aws-up.exit").write_text("9\n")
    proc = _run(tmp_path, "sleep 2; exit 0", detach=True)
    assert proc.returncode == 0
    assert _status(tmp_path)["state"] == "running"
    _wait_for(logs / "aws-up.exit")
    assert (logs / "aws-up.exit").read_text().strip() == "0"


@linux_only
@pytest.mark.parametrize("detach", [False, True])
def test_the_guard_reads_only_the_pid_field(tmp_path, detach):
    """A detached run writes "<pid> <start>". Reading the whole file as the
    pid made `kill -0` fail, so a second deploy started next to the first."""
    logs = _logs(tmp_path)
    running = subprocess.Popen(["bash", "-c", "exec -a rodeo-up sleep 30"])
    try:
        (logs / "aws-up.pid").write_text(f"{running.pid} 12345\n")
        proc = _run(tmp_path, "echo SHOULD-NOT-RUN; exit 0", detach=detach)
    finally:
        running.kill()
    assert proc.returncode == 75
    log = logs / "aws-up.log"
    assert "SHOULD-NOT-RUN" not in proc.stdout + (log.read_text() if log.exists() else "")
    assert "already running" in proc.stderr
    if detach:
        info = mod._marker_fields(proc.stdout, "AWS_UP_RUNNING")
        assert info["pid"] == running.pid


@linux_only
def test_status_does_not_take_a_reused_pid_for_the_deploy(tmp_path):
    logs = _logs(tmp_path)
    other = subprocess.Popen(["sleep", "30"])
    try:
        (logs / "aws-up.pid").write_text(f"{other.pid} 1\n")
        status = _status(tmp_path)
    finally:
        other.kill()
    assert status["state"] == "gone"


@linux_only
def test_status_with_nothing_recorded(tmp_path):
    _logs(tmp_path)
    assert _status(tmp_path)["state"] == "none"


# ---------- laptop side ----------

def _host() -> ProvisionedHost:
    return ProvisionedHost(id="primary", ssh="ec2-user@203.0.113.9", public_ip="203.0.113.9")


def _fake_remote(monkeypatch, rc: int, stdout: str):
    seen = []

    def fake_run_remote(inv, host, argv, **kw):
        seen.append(argv)
        return type("R", (), {"ok": rc == 0, "rc": rc, "stdout": stdout, "stderr": ""})()

    monkeypatch.setattr(mod, "run_remote", fake_run_remote)
    monkeypatch.setattr(mod, "resolve_ssh_identity", lambda **kw: "/tmp/key")
    return seen


def test_run_remote_up_detach_returns_the_record(monkeypatch):
    seen = _fake_remote(monkeypatch, 0, "noise\nAWS_UP_DETACHED pid=4242 start=987 log=/root/.rodeo/logs/aws-up.log\n")
    info = mod.run_remote_up({"name": "lab", "provider": {"type": "aws"}}, _host(), detach=True)
    assert info == {"state": "started", "pid": 4242, "start": "987",
                    "log": "/root/.rodeo/logs/aws-up.log", "host": "primary"}
    assert "tail -c" not in seen[0][-1]


def test_run_remote_up_detach_already_running_is_exit_75_with_json(monkeypatch):
    _fake_remote(monkeypatch, 75, "AWS_UP_RUNNING pid=77 log=/root/.rodeo/logs/aws-up.log\n")
    with pytest.raises(AlreadyRunning) as exc:
        mod.run_remote_up({"name": "lab", "provider": {"type": "aws"}}, _host(), detach=True)
    assert exc.value.info == {"state": "already_running", "pid": 77,
                              "log": "/root/.rodeo/logs/aws-up.log", "host": "primary"}


def test_remote_up_status_reads_the_saved_host(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "load_aws_host_state", lambda plan: {
        "host_id": "primary", "ssh": "ec2-user@203.0.113.9", "public_ip": "203.0.113.9"})
    seen = _fake_remote(monkeypatch, 0, "AWS_UP_STATUS state=exited pid= exit=0 log=/root/.rodeo/logs/aws-up.log\n"
                                        "AWS_UP_LOG\nline 1\nline 2\n")
    report = mod.remote_up_status({"name": "lab", "provider": {"type": "aws"}}, lines=9999)
    assert report == {"state": "exited", "pid": None, "exit": 0, "log": "/root/.rodeo/logs/aws-up.log",
                      "host": "primary", "ip": "203.0.113.9", "lines": ["line 1", "line 2"]}
    assert seen[0][-1] == "500"  # bounded


def test_remote_up_status_without_a_host(monkeypatch):
    monkeypatch.setattr(mod, "load_aws_host_state", lambda plan: None)
    with pytest.raises(mod.ConfigError, match="no cloud host"):
        mod.remote_up_status({"name": "lab", "provider": {"type": "aws"}})


# ---------- rodeo up --detach ----------

def _control_plane_stubs(monkeypatch, tmp_path):
    from rodeo.commands import up_cmd

    monkeypatch.setattr(up_cmd, "load_config", lambda *a, **k: {"name": "lab", "provider": {"type": "aws"}})
    monkeypatch.setattr(up_cmd, "_resolve_aws_instance_choice", lambda cfg, **k: cfg)
    monkeypatch.setattr(up_cmd, "validate_config", lambda cfg: None)
    monkeypatch.setattr(up_cmd, "_persist_plan_overlays", lambda lab, cfg: None)
    import rodeo.awscli
    import rodeo.host_context

    monkeypatch.setattr(rodeo.host_context, "apply_host_context", lambda cfg, host_facts: (cfg, []))
    monkeypatch.setattr(rodeo.awscli, "boto3_identity", lambda region: (True, "test"))
    monkeypatch.setattr(up_cmd, "execute_aws_up",
                        lambda *a, **k: pytest.fail("the blocking path ran"))
    return up_cmd


def test_aws_deploy_detached_writes_one_json_record(monkeypatch, tmp_path):
    up_cmd = _control_plane_stubs(monkeypatch, tmp_path)
    record = {"state": "started", "pid": 1, "start": "2", "log": "/root/x.log", "host": "primary"}
    monkeypatch.setattr(up_cmd, "execute_aws_up_detached", lambda cfg, **k: (_host(), record))
    out = io.StringIO()
    up_cmd._aws_control_plane_deploy(tmp_path, profile="test", instance_tier=None,
                                     assume_yes=True, detach_out=out)
    assert json.loads(out.getvalue()) == record
    assert out.getvalue().count("\n") == 1


def test_aws_deploy_detached_already_running_exits_75_with_json(monkeypatch, tmp_path):
    up_cmd = _control_plane_stubs(monkeypatch, tmp_path)
    info = {"state": "already_running", "pid": 9, "log": "/root/x.log", "host": "primary"}

    def boom(cfg, **k):
        raise AlreadyRunning(info)

    monkeypatch.setattr(up_cmd, "execute_aws_up_detached", boom)
    out = io.StringIO()
    with pytest.raises(SystemExit) as exc:
        up_cmd._aws_control_plane_deploy(tmp_path, profile="test", instance_tier=None,
                                         assume_yes=True, detach_out=out)
    assert exc.value.code == 75
    assert json.loads(out.getvalue()) == info


def test_up_detach_keeps_stdout_for_the_json(monkeypatch, capsys):
    from rodeo.commands import up_cmd

    def fake_up(**kw):
        assert kw["no_tmux"] is True
        up_cmd.console.print("Provisioning chatter")
        print("more chatter")
        kw["detach_out"].write('{"pid": 1}\n')

    monkeypatch.setattr(up_cmd, "_up", fake_up)
    up_cmd.up_cmd.callback(profile=None, name=None, lab_dir=None, assume_yes=True,
                           no_deploy=False, no_tmux=False, deployment_target="aws",
                           resume=False, reconcile=True, instance_tier=None, ref=None,
                           detach=True)
    captured = capsys.readouterr()
    assert captured.out == '{"pid": 1}\n'
    assert "Provisioning chatter" in captured.err and "more chatter" in captured.err


def test_up_without_detach_is_unchanged(monkeypatch):
    from rodeo.commands import up_cmd

    seen = {}
    monkeypatch.setattr(up_cmd, "_up", lambda **kw: seen.update(kw))
    up_cmd.up_cmd.callback(profile="test", name=None, lab_dir=None, assume_yes=True,
                           no_deploy=False, no_tmux=False, deployment_target="aws",
                           resume=False, reconcile=True, instance_tier=None, ref=None,
                           detach=False)
    assert "detach_out" not in seen and seen["no_tmux"] is False


def test_detach_refuses_a_local_deploy(monkeypatch, tmp_path, capsys):
    from rodeo.commands import up_cmd

    monkeypatch.setattr(up_cmd, "detect_host", lambda: None)
    with pytest.raises(SystemExit) as exc:
        up_cmd._up(profile=None, name=None, lab_dir=str(tmp_path), assume_yes=True,
                   no_deploy=False, no_tmux=True, deployment_target="baremetal", resume=False,
                   reconcile=True, instance_tier=None, ref=None, detach_out=io.StringIO())
    assert exc.value.code == 2
    assert "--detach only applies" in capsys.readouterr().out


# ---------- MCP ----------

@pytest.fixture
def no_ec2(monkeypatch):
    monkeypatch.setattr(mod, "on_ec2", lambda **kw: False)


def test_mcp_up_detaches_only_for_aws(no_ec2, tmp_path):
    up = mcp_tools.tool_map(allow_write=True)["rodeo_up"]
    assert up.argv({"target": "aws"}, tmp_path) == ["up", "--target", "aws", "--yes", "--no-tmux", "--detach"]
    assert up.argv({"target": "baremetal"}, tmp_path) == ["up", "--target", "baremetal", "--yes", "--no-tmux"]
    assert up.argv({"profile": "harvester"}, tmp_path) == ["up", "--profile", "harvester", "--yes", "--no-tmux"]


def test_mcp_up_reads_the_plan_target(no_ec2, tmp_path):
    (tmp_path / "rodeo-plan.yaml").write_text("deployment_target: aws\n")
    up = mcp_tools.tool_map(allow_write=True)["rodeo_up"]
    assert up.argv({}, tmp_path)[-1] == "--detach"
    assert up.argv({"target": "baremetal"}, tmp_path)[-1] == "--no-tmux"


def test_mcp_up_on_ec2_does_not_detach(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "on_ec2", lambda **kw: True)
    up = mcp_tools.tool_map(allow_write=True)["rodeo_up"]
    assert "--detach" not in up.argv({"target": "aws"}, tmp_path)


def test_mcp_up_detached_result_is_the_json(no_ec2, monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        return 0, '{"pid": 5, "state": "started"}\n'

    monkeypatch.setattr(mcp_tools, "run_rodeo", fake_run)
    up = mcp_tools.tool_map(allow_write=True)["rodeo_up"]
    ok, text = mcp_tools.call(up, {"target": "aws"}, lab_dir=tmp_path)
    assert ok and json.loads(text) == {"pid": 5, "state": "started"}
    assert calls[0][1]["stdout_only"] is True


def test_mcp_up_already_running_is_an_error_carrying_the_json(no_ec2, monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_tools, "run_rodeo", lambda argv, **kw: (75, '{"state": "already_running"}'))
    up = mcp_tools.tool_map(allow_write=True)["rodeo_up"]
    ok, text = mcp_tools.call(up, {"target": "aws"}, lab_dir=tmp_path)
    assert not ok and json.loads(text) == {"state": "already_running"}


def test_mcp_local_up_does_not_ask_for_stdout_only(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(mcp_tools, "run_rodeo", lambda argv, **kw: calls.append(kw) or (0, "ok"))
    up = mcp_tools.tool_map(allow_write=True)["rodeo_up"]
    mcp_tools.call(up, {"target": "baremetal"}, lab_dir=tmp_path)
    assert "stdout_only" not in calls[0]


def test_mcp_status_cloud_argv():
    status = mcp_tools.tool_map(allow_write=False)["rodeo_status"]
    assert status.argv({"cloud": True, "lines": 20, "output": "json"}) == \
        ["status", "--output", "json", "--cloud", "--lines", "20"]


def test_run_rodeo_stdout_only(tmp_path, monkeypatch):
    from rodeo.mcp import exec as mcp_exec

    script = tmp_path / "fake"
    script.write_text("#!/bin/sh\necho chatter >&2\necho '{\"pid\": 1}'\n")
    script.chmod(0o755)
    monkeypatch.setattr(mcp_exec, "rodeo_argv", lambda args: [str(script)])
    assert mcp_exec.run_rodeo(["up"], stdout_only=True) == (0, '{"pid": 1}\n')
    code, out = mcp_exec.run_rodeo(["up"])
    assert "chatter" in out
