"""ClusterPhase derives start order / ready count / etcd gap from the definition."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from rodeo.config import ConfigError
from rodeo.engine.cluster import ClusterPhase
from rodeo.profiles.suse_virt import SuseVirtProfile

EXAMPLES = Path(__file__).parent.parent / "rodeo" / "data" / "examples"


def _cfg(config_dir=None, vms=None):
    cfg = {
        "type": "suse-virt",
        "network": {"vip": "192.168.122.10"},
        "libvirt": {"uri": "test:///default"},
        "vms": vms or {},
    }
    if config_dir:
        cfg["config_dir"] = str(config_dir)
    return cfg


def test_two_node_topology_from_definition():
    cfg = _cfg(
        EXAMPLES / "harvester-lab-config",
        {"harvester1": {"ip": "192.168.122.11"}, "harvester2": {"ip": "192.168.122.12"}},
    )
    cp = ClusterPhase(cfg)
    assert cp.ready_count == 2
    assert cp.start_order == ["harvester1", "harvester2"]
    assert cp.harvester_nodes == ["harvester1", "harvester2"]
    assert cp.etcd_gap == 90


def test_three_node_topology_from_bundled_definition():
    cfg = _cfg(vms={f"harvester{i}": {} for i in (1, 2, 3)} | {"rancher": {}})
    cp = ClusterPhase(cfg)
    assert cp.ready_count == 3
    assert cp.start_order[:3] == ["harvester1", "harvester2", "harvester3"]
    assert "rancher" in cp.start_order
    assert "rancher" not in cp.harvester_nodes


class _FakeRunner:
    def __init__(self, cfg):
        self.cfg = cfg
        self._last_rc = -1
        self.rancher_called = False

    def stream_rancher(self):
        self.rancher_called = True
        self._last_rc = 0
        return iter(())


def test_rancher_phase_skipped_without_rancher_node():
    profile = SuseVirtProfile()
    runner = _FakeRunner({"vms": {"harvester1": {}, "harvester2": {}}})
    list(profile.run_phase("rancher", runner, Path("/tmp/x")))
    assert runner.rancher_called is False
    assert runner._last_rc == 0


def test_rancher_phase_runs_with_rancher_node():
    profile = SuseVirtProfile()
    runner = _FakeRunner({"vms": {"harvester1": {}, "rancher": {}}})
    list(profile.run_phase("rancher", runner, Path("/tmp/x")))
    assert runner.rancher_called is True


def test_load_topology_raises_on_missing_definition(monkeypatch):
    def _raise(_cfg):
        raise FileNotFoundError("definition.yaml missing")

    monkeypatch.setattr("rodeo.inventory.build_inventory", _raise)
    with pytest.raises(FileNotFoundError, match="definition.yaml missing"):
        ClusterPhase(_cfg(vms={"harvester1": {}}))


def test_load_topology_raises_on_config_error(monkeypatch):
    def _raise(_cfg):
        raise ConfigError("bad topology")

    monkeypatch.setattr("rodeo.inventory.build_inventory", _raise)
    with pytest.raises(ConfigError, match="bad topology"):
        ClusterPhase(_cfg(vms={"harvester1": {}}))


def test_load_topology_warns_on_unexpected_inventory_error(monkeypatch, caplog):
    cfg = _cfg(vms={"harvester1": {}, "harvester2": {}})

    def _raise(_cfg):
        raise RuntimeError("transient render failure")

    monkeypatch.setattr("rodeo.inventory.build_inventory", _raise)
    with caplog.at_level(logging.WARNING):
        cp = ClusterPhase(cfg)
    assert "using cfg fallback" in caplog.text
    assert cp.harvester_nodes == ["harvester1", "harvester2"]
    assert cp.ready_count == 2
