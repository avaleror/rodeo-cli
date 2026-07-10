"""stream_apply must run kubectl under sudo with the node kubeconfig.

Harvester nodes SSH in as the non-root `rancher` user, but the RKE2 kubeconfig
is root-only. A plain `kubectl apply` fails with "permission denied". This locks
in that the apply phase wraps kubectl in sudo, selects the RKE2/K3s kubeconfig,
and streams the manifest over stdin — the fix validated live on a real lab.
"""
from __future__ import annotations

import subprocess

from rodeo.engine.runner import DeployRunner, LogLine


def _drain(gen):
    return list(gen)


def test_apply_uses_sudo_kubectl_with_node_kubeconfig(tmp_path, monkeypatch):
    # A manifest dir named after the harvester1 host, with one namespace file.
    host_dir = tmp_path / "harvester1"
    host_dir.mkdir()
    (host_dir / "namespaces.yml").write_text("apiVersion: v1\nkind: Namespace\nmetadata:\n  name: demo\n")

    cfg = {
        "type": "suse-virt",
        "name": "t",
        "config_dir": str(tmp_path),
        "vms": {"harvester1": {"ip": "10.0.0.11", "user": "rancher"}},
        "ssh": {"identity_file": "/root/.ssh/id_ed25519"},
    }

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(argv, 0, stdout="namespace/demo created", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = DeployRunner(cfg, tmp_path)
    events = _drain(runner.stream_apply())

    argv = captured["argv"]
    assert argv[0] == "ssh"
    assert "rancher@10.0.0.11" in argv  # SSHes as the node's user
    remote = argv[-1]
    # kubectl must run under sudo, picking the RKE2 (Harvester) or K3s kubeconfig.
    assert "sudo" in remote
    assert "kubectl apply -f -" in remote
    assert "/etc/rancher/rke2/rke2.yaml" in remote
    assert "/etc/rancher/k3s/k3s.yaml" in remote
    # The manifest body is streamed over stdin, not argv.
    assert "kind: Namespace" in captured["input"]
    # Success is reported.
    assert any(isinstance(e, LogLine) and "applied" in e.line for e in events)


def test_apply_is_noop_without_manifest_dirs(tmp_path, monkeypatch):
    # A profile like harvester-2n bundles no per-host manifests: apply must be a
    # clean no-op (no ssh call), not an error.
    called = {"n": 0}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.__setitem__("n", called["n"] + 1))

    cfg = {"type": "suse-virt", "name": "t", "config_dir": str(tmp_path), "vms": {}}
    runner = DeployRunner(cfg, tmp_path)
    _drain(runner.stream_apply())

    assert called["n"] == 0
    assert runner._last_rc == 0
