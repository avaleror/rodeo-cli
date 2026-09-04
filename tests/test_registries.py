"""Extension registries: register_profile / register_stream_phase /
register_provider, plus lazy rodeo.plugins entry-point discovery."""
from __future__ import annotations

import pytest

import rodeo.host_context as host_ctx_mod
import rodeo.plugins as plugins_mod
import rodeo.profiles as profiles_mod
import rodeo.providers.registry as providers_mod
from rodeo.config import ConfigError, validate_config
from rodeo.host_context import (
    apply_host_context,
    is_known_target,
    register_host_context,
)
from rodeo.profiles import base as profiles_base
from rodeo.profiles import get_profile, list_profile_types, register_profile
from rodeo.profiles.base import RodeoProfile, register_stream_phase
from rodeo.providers.registry import get_provider, list_providers, register_provider


@pytest.fixture(autouse=True)
def isolated_registries(monkeypatch):
    """Snapshot the global registries so registrations don't leak between tests."""
    monkeypatch.setattr(profiles_mod, "_REGISTRY", dict(profiles_mod._REGISTRY))
    monkeypatch.setattr(providers_mod, "_FACTORIES", dict(providers_mod._FACTORIES))
    monkeypatch.setattr(profiles_base, "_STREAM_PHASES", dict(profiles_base._STREAM_PHASES))
    monkeypatch.setattr(host_ctx_mod, "_TARGETS", dict(host_ctx_mod._TARGETS))
    monkeypatch.setattr(plugins_mod, "_loaded", False)


class _StubProfile(RodeoProfile):
    name = "stub-lab"
    phases = ["alpha"]
    vm_names = ["vm1"]
    ansible_phases = frozenset()

    def default_cfg(self, config_dir=None):
        return {"vms": {"vm1": {"ip": "10.0.0.1", "user": "root"}}}


# ---------- profiles ----------

def test_register_profile_resolves_by_type():
    register_profile(_StubProfile())
    assert isinstance(get_profile("stub-lab"), _StubProfile)
    assert "stub-lab" in list_profile_types()


def test_register_profile_rejects_duplicates_unless_replace():
    register_profile(_StubProfile())
    with pytest.raises(ValueError, match="already registered"):
        register_profile(_StubProfile())
    register_profile(_StubProfile(), replace=True)


def test_register_profile_requires_a_name():
    profile = _StubProfile()
    profile.name = ""
    with pytest.raises(ValueError, match="non-empty"):
        register_profile(profile)


def test_unknown_profile_still_raises():
    with pytest.raises(ValueError, match="Unknown rodeo type"):
        get_profile("no-such-lab")


# ---------- stream phases ----------

def test_register_stream_phase_dispatches_to_runner_method():
    register_stream_phase("liab", "stream_liab")
    assert profiles_base._STREAM_PHASES["liab"] == ("stream_liab", False)

    class _Runner:
        cfg = {"vms": {"vm1": {}}}
        _last_rc = None

        def stream_liab(self):
            self._last_rc = 0
            yield "event"

    profile = _StubProfile()
    runner = _Runner()
    events = list(profile.run_phase("liab", runner, vars_file=None))
    assert events == ["event"]
    assert runner._last_rc == 0


def test_register_stream_phase_rejects_duplicates_unless_replace():
    with pytest.raises(ValueError, match="already registered"):
        register_stream_phase("rancher", "stream_other")
    register_stream_phase("rancher", "stream_other", replace=True)
    assert profiles_base._STREAM_PHASES["rancher"] == ("stream_other", False)


# ---------- providers ----------

def test_register_provider_and_get():
    sentinel = object()
    register_provider("lab-in-a-box", lambda: sentinel)
    assert get_provider("lab-in-a-box") is sentinel
    assert "lab-in-a-box" in list_providers()


def test_register_provider_can_implement_a_planned_name():
    sentinel = object()
    register_provider("gcp", lambda: sentinel)
    assert get_provider("gcp") is sentinel


def test_planned_provider_message_preserved():
    with pytest.raises(ConfigError, match=r"planned \(F4b\)"):
        get_provider("gcp")


def test_unknown_provider_lists_known_types():
    with pytest.raises(ConfigError, match="unsupported provider.type"):
        get_provider("nope")


def test_register_provider_rejects_duplicates_unless_replace():
    register_provider("x", lambda: 1)
    with pytest.raises(ValueError, match="already registered"):
        register_provider("x", lambda: 2)
    register_provider("x", lambda: 2, replace=True)
    assert get_provider("x") == 2


# ---------- host contexts (deployment_target) ----------

def test_register_host_context_applies_overlay():
    def overlay(cfg, facts):
        cfg.setdefault("libvirt", {})["disk_cache"] = "writeback"
        return ["libvirt.disk_cache: → writeback (my-cloud)"]

    register_host_context("my-cloud", overlay)
    assert is_known_target("my-cloud")
    out, notes = apply_host_context({"deployment_target": "my-cloud"})
    assert out["libvirt"]["disk_cache"] == "writeback"
    assert any("my-cloud" in n for n in notes)


def test_registered_target_passes_validation():
    register_host_context("my-cloud", lambda cfg, facts: [])
    validate_config({"deployment_target": "my-cloud"})


def test_unknown_target_fails_validation_listing_known():
    with pytest.raises(ConfigError, match="Invalid deployment_target 'nope'.*baremetal"):
        validate_config({"deployment_target": "nope"})


def test_register_host_context_rejects_duplicates_unless_replace():
    with pytest.raises(ValueError, match="already registered"):
        register_host_context("aws", lambda c, f: [])
    register_host_context("aws", lambda c, f: ["aws override"], replace=True)
    _out, notes = apply_host_context({"deployment_target": "aws"})
    assert "aws override" in notes


def test_unregistered_target_shapes_like_baremetal():
    out, _notes = apply_host_context({"deployment_target": "mystery"})
    assert out["deployment_target"] == "mystery"


# ---------- entry-point discovery ----------

class _FakeEntryPoint:
    name = "fake-plugin"

    def load(self):
        def _register():
            register_profile(_StubProfile())

        return _register


def test_plugin_entry_point_is_discovered_on_lookup_miss(monkeypatch):
    monkeypatch.setattr(
        plugins_mod.metadata, "entry_points", lambda group: [_FakeEntryPoint()]
    )
    # Not registered yet — the miss triggers discovery, which registers it.
    assert isinstance(get_profile("stub-lab"), _StubProfile)


def test_broken_plugin_is_skipped_not_fatal(monkeypatch):
    class _Broken:
        name = "broken"

        def load(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        plugins_mod.metadata, "entry_points", lambda group: [_Broken(), _FakeEntryPoint()]
    )
    assert isinstance(get_profile("stub-lab"), _StubProfile)


def test_plugins_load_only_once(monkeypatch):
    calls = []
    monkeypatch.setattr(
        plugins_mod.metadata, "entry_points",
        lambda group: calls.append(group) or [],
    )
    with pytest.raises(ValueError):
        get_profile("no-such-lab")
    with pytest.raises(ConfigError):
        get_provider("no-such-provider")
    assert calls == [plugins_mod.GROUP]
