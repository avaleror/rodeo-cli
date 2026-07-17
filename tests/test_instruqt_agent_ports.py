"""Instruqt hostimages need firewalld to allow agent ports 15778/15779.

Without them, a cold boot after Save sticks the console on "Please Wait"
even though SSH may still be open. Bare metal must not gain these rules.
"""
from __future__ import annotations

from pathlib import Path

import yaml

import rodeo
from rodeo.engine.runner import DeployRunner, LogLine

_FIREWALL = (
    Path(rodeo.__file__).parent
    / "data"
    / "ansible"
    / "roles"
    / "kvm_host"
    / "tasks"
    / "firewall.yml"
)


def test_firewall_yml_opens_instruqt_agent_ports_only_for_instruqt():
    text = _FIREWALL.read_text()
    assert "15778" in text
    assert "15779" in text
    assert "deployment_target" in text

    docs = list(yaml.safe_load_all(text))
    tasks = docs[0] if isinstance(docs[0], list) else docs
    agent = next(t for t in tasks if t.get("name") == "Allow Instruqt agent ports on the public zone")
    assert agent["when"] == "deployment_target | default('baremetal') == 'instruqt'"
    assert agent["ansible.posix.firewalld"]["permanent"] is True
    assert agent["ansible.posix.firewalld"]["immediate"] is False
    assert agent["ansible.posix.firewalld"]["state"] == "enabled"
    assert agent["loop"] == ["15778", "15779"]


def test_write_vars_file_passes_deployment_target(fake_cfg, tmp_path, monkeypatch):
    fake_cfg["deployment_target"] = "instruqt"
    # Fake profile has no definition.yaml; stub inventory like test_runner does.
    monkeypatch.setattr("rodeo.inventory.build_inventory", lambda cfg: {})
    data = yaml.safe_load(DeployRunner(fake_cfg, tmp_path)._write_vars_file().read_text())
    assert data["deployment_target"] == "instruqt"


def test_start_firewalld_opens_agent_ports_on_instruqt(fake_cfg, tmp_path, monkeypatch):
    fake_cfg["deployment_target"] = "instruqt"
    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(list(cmd))

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr("rodeo.engine.runner.subprocess.run", _run)
    monkeypatch.setattr("rodeo.engine.runner._detect_ext_iface", lambda: "ens4")

    events = list(DeployRunner(fake_cfg, tmp_path)._start_firewalld())
    lines = [e.line for e in events if isinstance(e, LogLine)]
    assert any("15778/15779" in ln for ln in lines)

    port_calls = [
        c for c in calls
        if c[:1] == ["firewall-cmd"] and any(a.startswith("--add-port=") for a in c)
    ]
    assert ["firewall-cmd", "--zone=public", "--add-port=15778/tcp", "--permanent"] in port_calls
    assert ["firewall-cmd", "--zone=public", "--add-port=15779/tcp", "--permanent"] in port_calls
    assert ["firewall-cmd", "--zone=public", "--change-interface=ens4", "--permanent"] in calls


def test_start_firewalld_skips_agent_ports_on_baremetal(fake_cfg, tmp_path, monkeypatch):
    fake_cfg["deployment_target"] = "baremetal"
    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(list(cmd))

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr("rodeo.engine.runner.subprocess.run", _run)

    list(DeployRunner(fake_cfg, tmp_path)._start_firewalld())
    port_calls = [
        c for c in calls
        if c[:1] == ["firewall-cmd"] and any("15778" in a or "15779" in a for a in c)
    ]
    assert port_calls == []
