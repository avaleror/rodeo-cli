"""rodeo.engine.libvirt — availability is re-checked, not cached forever."""
from __future__ import annotations

import sys
import types

import pytest

import rodeo.engine.libvirt as lv_mod


@pytest.fixture(autouse=True)
def _restore_module_state():
    """Each test flips module globals / sys.modules — restore after."""
    saved = (lv_mod._libvirt, lv_mod._AVAILABLE, lv_mod._VIR_RUNNING, lv_mod._VIR_ERR_NO_DOMAIN)
    had_libvirt = "libvirt" in sys.modules
    saved_libvirt_mod = sys.modules.get("libvirt")
    yield
    lv_mod._libvirt, lv_mod._AVAILABLE, lv_mod._VIR_RUNNING, lv_mod._VIR_ERR_NO_DOMAIN = saved
    if had_libvirt:
        sys.modules["libvirt"] = saved_libvirt_mod
    else:
        sys.modules.pop("libvirt", None)


def test_require_libvirt_retries_after_install_deps_mid_process(monkeypatch):
    """Simulates the AWS live-run failure: this module's own top-level import
    ran (and failed) before `install-deps` installed the package later in the
    same `rodeo up` process. A stale cached "unavailable" must not survive
    the package becoming importable — `_require_libvirt` (and therefore
    `LibvirtDriver()`) has to succeed once it genuinely can be imported."""
    lv_mod._libvirt = None
    lv_mod._AVAILABLE = False
    sys.modules.pop("libvirt", None)

    with pytest.raises(RuntimeError, match="libvirt-python not installed"):
        lv_mod._require_libvirt()

    fake_libvirt = types.SimpleNamespace(
        VIR_DOMAIN_RUNNING=1,
        VIR_ERR_NO_DOMAIN=42,
        registerErrorHandler=lambda *a, **k: None,
    )
    sys.modules["libvirt"] = fake_libvirt

    lv_mod._require_libvirt()  # must not raise now
    assert lv_mod._AVAILABLE is True
    assert lv_mod._libvirt is fake_libvirt


def test_load_libvirt_does_not_reimport_once_available(monkeypatch):
    """Once available, _load_libvirt short-circuits without touching sys.modules."""
    sentinel = types.SimpleNamespace()
    lv_mod._libvirt = sentinel
    lv_mod._AVAILABLE = True

    assert lv_mod._load_libvirt() is True
    assert lv_mod._libvirt is sentinel  # unchanged, no reimport attempted
