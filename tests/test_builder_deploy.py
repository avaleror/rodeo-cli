"""Rodeo Builder: deploying a rodeo without saving it (rodeo/builder/deploywork.py)."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from rodeo.builder import deploywork
from rodeo.builder.api import Api

FILES = [{"path": "rodeo-plan.yaml", "content": "name: x\ntype: rancher\ndeployment_target: baremetal\n"},
         {"path": "story/01-hello.md", "content": "# Hello\n"},
         {"path": "checks/check-hello.sh", "content": "#!/bin/sh\nexit 0\n", "executable": True}]

FAKE_RODEO = '''import os, signal, sys, time
lab = os.path.basename(sys.argv[-1])
print("rodeo", " ".join(sys.argv[1:]), flush=True)
signal.signal(signal.SIGINT, lambda *a: (print("interrupted", flush=True), sys.exit(130)))
for i in range(300 if lab.startswith("slow-") else 3):
    print("phase", i, flush=True)
    time.sleep(0.1)
sys.exit(1 if lab.startswith("fail-") else 0)
'''


@pytest.fixture
def fake_rodeo(tmp_path, monkeypatch):
    script = tmp_path / "fake-rodeo"
    script.write_text("#!{}\n{}".format(sys.executable, FAKE_RODEO))
    script.chmod(0o755)
    monkeypatch.setattr(deploywork, "find_rodeo_bin", lambda: str(script))
    monkeypatch.setattr(deploywork, "capability", lambda run=None: {"available": True, "reason": "", "as": "root"})
    return script


def _wait(deployer, until=("succeeded", "failed", "stopped"), timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = deployer.status()
        if state["status"] in until:
            return state
        time.sleep(0.1)
    raise AssertionError("deploy did not finish: {}".format(deployer.status()))


def test_capability_needs_root_or_passwordless_sudo(monkeypatch):
    monkeypatch.setattr(deploywork.os, "geteuid", lambda: 0)
    assert deploywork.capability() == {"available": True, "reason": "", "as": "root"}
    monkeypatch.setattr(deploywork.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(deploywork.shutil, "which", lambda name: "/usr/bin/sudo")
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0 if len(calls) == 1 else 1, "", "")

    assert deploywork.capability(run)["as"] == "sudo" and calls[0] == ["sudo", "-n", "true"]
    refused = deploywork.capability(run)
    assert not refused["available"] and "passwordless sudo" in refused["reason"]


def test_start_deploys_a_copy_and_reports_progress(fake_rodeo):
    deployer = deploywork.Deployer()
    assert deployer.status() is None
    state = deployer.start({"name": "ride", "files": FILES})
    lab = Path(state["lab"])
    assert lab.parent == deploywork.labs_root() and lab.name.startswith("ride-unsaved-")
    assert yaml.safe_load((lab / "rodeo-plan.yaml").read_text())["name"] == "ride"
    assert (lab / "checks" / "check-hello.sh").stat().st_mode & 0o111
    assert not (deploywork.invoking_home() / ".rodeo" / "profiles" / "ride").exists()
    done = _wait(deployer)
    assert done["status"] == "succeeded" and done["rc"] == 0
    assert done["log"].splitlines()[0] == "rodeo up --yes --no-tmux --dir {}".format(lab)
    assert "phase 2" in done["log"]


def test_stop_failure_and_one_at_a_time(fake_rodeo):
    deployer = deploywork.Deployer()
    deployer.start({"name": "slow", "files": FILES})
    deadline = time.monotonic() + 10
    while "phase 0" not in deployer.status()["log"] and time.monotonic() < deadline:
        time.sleep(0.05)
    with pytest.raises(RuntimeError, match="already running"):
        deployer.start({"name": "other", "files": FILES})
    deployer.stop()
    stopped = _wait(deployer)
    assert stopped["status"] == "stopped" and "interrupted" in stopped["log"]
    deployer.start({"name": "fail", "files": FILES})
    failed = _wait(deployer)
    assert failed["status"] == "failed" and failed["rc"] == 1


def test_start_asks_for_operator_secrets_and_stores_them(fake_rodeo, monkeypatch):
    plan = "name: x\ntype: rancher\ndeployment_target: baremetal\noperator_secrets: [scc_regcode]\n" \
           "credentials:\n  regcode: ??scc_regcode\n  admin: ??admin_pwd\n"
    files = [{"path": "rodeo-plan.yaml", "content": plan}]
    deployer = deploywork.Deployer()
    with pytest.raises(deploywork.NeedSecrets) as err:
        deployer.start({"name": "ride", "files": files})
    assert err.value.keys == ["scc_regcode"] and not list(deploywork.labs_root().glob("ride-*"))
    state = deployer.start({"name": "ride", "files": files, "secrets": {"scc_regcode": "REG-123"}})
    lab = Path(state["lab"])
    assert "REG-123" in deploywork.secrets_path().read_text() and "REG-123" not in (lab / "rodeo-plan.yaml").read_text()
    assert "admin_pwd" in (lab / ".rodeo-secrets.yaml").read_text()
    _wait(deployer)


def test_start_refuses_bad_requests_and_no_root(fake_rodeo, monkeypatch):
    deployer = deploywork.Deployer()
    with pytest.raises(ValueError):
        deployer.start({"name": "Bad Name", "files": FILES})
    with pytest.raises(ValueError, match="rodeo-plan.yaml"):
        deployer.start({"name": "ride", "files": FILES[1:]})
    with pytest.raises(FileNotFoundError):
        deployer.start({"name": "ride", "files": FILES, "base": "no-such-profile"})
    monkeypatch.setattr(deploywork, "capability", lambda run=None: {"available": False, "reason": deploywork.NEEDS_ROOT, "as": ""})
    with pytest.raises(PermissionError, match="needs root"):
        deployer.start({"name": "ride", "files": FILES})
    assert deployer.status() is None


def test_api_deploys_only_on_a_loopback_live_server(fake_rodeo):
    body = json.dumps({"name": "ride", "files": FILES}).encode()
    assert Api().dispatch("deploy", "POST", body=body) == (403, {"error": "deploying needs the rodeo builder server"})
    status, answer = Api(can_save=True).dispatch("deploy", "POST", body=body)
    assert status == 403 and "listens on this machine only" in answer["error"]
    assert Api(can_save=True).dispatch("deploy")[1]["available"] is False
    live = Api(can_save=True, can_deploy=True)
    status, info = live.dispatch("deploy")
    assert status == 200 and info["available"] and info["current"] is None
    status, answer = live.dispatch("deploy", "POST", body=body)
    assert status == 200 and answer["current"]["status"] == "running"
    assert live.dispatch("deploy", "POST", body=body)[0] == 409
    _wait(live.deployer)
    secrets_plan = json.dumps({"name": "ride", "files": [{"path": "rodeo-plan.yaml", "content":
                               "name: x\ntype: rancher\noperator_secrets: [k]\ncredentials:\n  a: ??k\n"}]}).encode()
    assert live.dispatch("deploy", "POST", body=secrets_plan) == (200, {
        "current": None, "secrets": ["k"], "message": "this lab needs secrets only you can provide: k"})
    assert live.dispatch("deploy-stop", "POST", body=b"{}")[0] == 200


REFS_PLAN = [{"path": "rodeo-plan.yaml", "content": "name: x\ntype: rancher\ndeployment_target: baremetal\n"
              "operator_secrets: [scc_regcode]\ncredentials:\n  regcode: ??scc_regcode\n"}]


def _secrets_yaml(text: str) -> None:
    path = deploywork.secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_secret_refs_take_values_from_secrets_yaml(fake_rodeo):
    _secrets_yaml('my_regcode: "REG-FROM-FILE"\n')
    deployer = deploywork.Deployer()
    deployer.start({"name": "ride", "files": REFS_PLAN, "secret_refs": {"scc_regcode": "my_regcode"}})
    assert yaml.safe_load(deploywork.secrets_path().read_text()) == {
        "my_regcode": "REG-FROM-FILE", "scc_regcode": "REG-FROM-FILE"}
    _wait(deployer)


def test_explicit_secrets_win_over_secret_refs(fake_rodeo):
    _secrets_yaml('my_regcode: "REG-FROM-FILE"\n')
    deployer = deploywork.Deployer()
    deployer.start({"name": "ride", "files": REFS_PLAN, "secrets": {"scc_regcode": "REG-TYPED"},
                    "secret_refs": {"scc_regcode": "my_regcode"}})
    assert yaml.safe_load(deploywork.secrets_path().read_text())["scc_regcode"] == "REG-TYPED"
    _wait(deployer)


@pytest.mark.parametrize("refs,match", [
    ({"scc_regcode": "nope"}, "has no key nope"),
    ({"scc_regcode": "nested"}, "has no key nested"),
    (["scc_regcode"], "secret_refs must map"),
    ({"scc_regcode": 5}, "secret_refs must map"),
])
def test_bad_secret_refs_are_refused(fake_rodeo, refs, match):
    _secrets_yaml('my_regcode: "R"\nnested:\n  a: 1\n')
    with pytest.raises(ValueError, match=match):
        deploywork.Deployer().start({"name": "ride", "files": REFS_PLAN, "secret_refs": refs})
    assert not list(deploywork.labs_root().glob("ride-*"))
    status, answer = Api(can_save=True, can_deploy=True).dispatch(
        "deploy", "POST", body=json.dumps({"name": "ride", "files": REFS_PLAN, "secret_refs": refs}).encode())
    assert status == 400 and match in answer["error"]


def test_secret_refs_do_not_cover_other_secrets(fake_rodeo):
    _secrets_yaml('my_regcode: "R"\n')
    with pytest.raises(deploywork.NeedSecrets) as err:
        deploywork.Deployer().start({"name": "ride", "files": REFS_PLAN, "secret_refs": {}})
    assert err.value.keys == ["scc_regcode"]
