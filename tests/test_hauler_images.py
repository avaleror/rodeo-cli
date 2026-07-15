"""Hauler must mirror images that actually exist at registry.suse.com.

Regression: rancher/elemental-register was never a real repository there
(confirmed live: registry returns NAME_UNKNOWN) — the register agent ships
inside the elemental-operator image itself, same tag as the operator's own
Deployment (confirmed live: that exact image+tag pulls fine).
"""
from __future__ import annotations

from pathlib import Path

import rodeo.engine.rancher as rancher_mod


def test_hauler_does_not_reference_nonexistent_elemental_register_image():
    source = Path(rancher_mod.__file__).read_text()
    assert "rancher/elemental-register:" not in source
    assert "rancher/elemental-operator:{self.elemental_op_version}" in source
