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
    assert harv["version"] == "1.8.2"
    assert harv["repo"]["name"] == "rancher"
    assert harv["repo"]["git_repo"] == "https://github.com/rancher/ui-plugin-charts"


def test_suse_virt_versions_bumped_without_touching_suse_edge():
    """2026-09-16: Harvester 1.8.1 -> 1.8.2, Rancher 2.14.1 -> 2.14.5 for the
    suse-virt (Harvester-family) profiles only. SUSE Edge stays on Rancher
    2.14.1 for now — its own versions dict extends the same shared
    BASE_VERSIONS constant, so this guards against a future edit to
    BASE_VERSIONS accidentally dragging Edge's Rancher version along."""
    from rodeo.profiles.suse_edge import SuseEdgeProfile

    virt_versions = SuseVirtProfile().default_cfg().get("versions", {})
    assert virt_versions["harvester"] == "1.8.2"
    assert virt_versions["rancher"] == "2.14.5"

    edge_versions = SuseEdgeProfile().default_cfg().get("versions", {})
    assert edge_versions["rancher"] == "2.14.1"


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


def test_bundled_harvester_and_harvester_2n_examples_declare_ui_extensions():
    """Regression: 'rodeo up --profile harvester' seeds a lab from
    rodeo/data/examples/<name>/definition.yaml, NOT the canonical
    rodeo/data/platforms/suse-virt/definition.yaml — build_inventory() prefers a
    config_dir's own definition.yaml when present (rodeo/inventory.py
    _load_topology). The canonical file got rancher.ui_extensions in #34, but the
    bundled example copies (which every real 'rodeo up'/'rodeo new --from'
    deploy actually uses) never did, so the reconcile step silently never ran for
    any real deployment. Covers the two bundled profiles that have both a
    Harvester cluster and a Rancher VM — the only ones the Harvester UI
    extension is meaningful for (harvester-lab-config/harvester-ha-config have
    no Rancher VM; rancher-lab-config has no Harvester cluster to import)."""
    from rodeo.labseed import example_dir

    for profile in ("harvester", "harvester-2n"):
        cfg = SuseVirtProfile().default_cfg(config_dir=str(example_dir(profile)))
        exts = cfg.get("rancher_ui_extensions")
        assert exts, f"bundled '{profile}' example should declare rancher_ui_extensions"
        harv = next(e for e in exts if e["name"] == "harvester")
        assert harv["version"] == "1.8.2"
