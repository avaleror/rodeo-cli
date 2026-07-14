"""Profile inventory loading and static-VM fallback behavior."""
from __future__ import annotations

import logging

import pytest

from rodeo.config import ConfigError
from rodeo.profiles.suse_virt import SuseVirtProfile


def test_load_inventory_raises_on_missing_definition(monkeypatch):
    profile = SuseVirtProfile()

    def _raise(_cfg):
        raise FileNotFoundError("definition.yaml missing")

    monkeypatch.setattr("rodeo.profiles.base._inv.build_inventory", _raise)
    with pytest.raises(FileNotFoundError, match="definition.yaml missing"):
        profile._load_inventory(None)


def test_load_inventory_raises_on_config_error(monkeypatch):
    profile = SuseVirtProfile()

    def _raise(_cfg):
        raise ConfigError("bad topology")

    monkeypatch.setattr("rodeo.profiles.base._inv.build_inventory", _raise)
    with pytest.raises(ConfigError, match="bad topology"):
        profile._load_inventory(None)


def test_load_inventory_raises_on_value_error(monkeypatch):
    profile = SuseVirtProfile()

    def _raise(_cfg):
        raise ValueError("definition.yaml must contain a top-level 'definition:' key")

    monkeypatch.setattr("rodeo.profiles.base._inv.build_inventory", _raise)
    with pytest.raises(ValueError, match="definition:"):
        profile._load_inventory(None)


def test_default_cfg_warns_on_static_fallback(monkeypatch, caplog):
    profile = SuseVirtProfile()

    def _raise(_cfg):
        raise RuntimeError("transient render failure")

    monkeypatch.setattr("rodeo.profiles.base._inv.build_inventory", _raise)
    with caplog.at_level(logging.WARNING):
        cfg = profile.default_cfg()
    assert "falling back to static VM inventory" in caplog.text
    assert cfg["vms"] == profile.static_vms