"""Rancher UI extensions are declared in the definition and reach the RancherPhase."""
from __future__ import annotations

from rodeo.engine.rancher import RancherPhase
from rodeo.profiles.suse_virt import SuseVirtProfile


def test_suse_virt_declares_harvester_ui_extension():
    """The bundled suse-virt definition pins the Harvester UI extension."""
    cfg = SuseVirtProfile().default_cfg()
    exts = cfg.get("rancher_ui_extensions")
    assert exts, "suse-virt should declare rancher_ui_extensions"
    harv = next(e for e in exts if e["name"] == "harvester")
    assert harv["version"] == "1.8.1"
    assert harv["repo"]["name"] == "rancher"
    assert harv["repo"]["git_repo"] == "https://github.com/rancher/ui-plugin-charts"


def test_rancher_phase_reads_ui_extensions():
    """The engine exposes the declared extensions for reconcile."""
    cfg = SuseVirtProfile().default_cfg()
    cfg.setdefault("network", {})
    phase = RancherPhase(cfg)
    assert [e["name"] for e in phase.ui_extensions] == ["harvester"]


def test_ui_extensions_default_empty_without_declaration():
    """A profile/plan that declares none gets an empty list, not an error."""
    cfg = {"network": {}, "vms": {}}
    phase = RancherPhase(cfg)
    assert phase.ui_extensions == []
