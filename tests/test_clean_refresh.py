"""Guards for `rodeo clean` NOT touching the CLI version unless asked.

`clean` used to always `git pull --ff-only` + reinstall at the end, which made
the CLI version non-deterministic on pinned/Instruqt hosts and used the stale-
prone update path. These tests lock in that the refresh is opt-in only.
"""
from __future__ import annotations

import inspect

from rodeo.commands import clean as clean_mod


def test_refresh_flag_exists_and_defaults_off():
    params = {p.name: p for p in clean_mod.clean_cmd.params}
    assert "refresh" in params, "clean must expose a --refresh flag"
    assert params["refresh"].default is False, "refresh must be opt-in (default off)"


def test_no_unconditional_cli_pull_remains():
    src = inspect.getsource(clean_mod.clean_cmd.callback)
    # The old always-on refresh used a bare git pull --ff-only. It must be gone.
    assert "--ff-only" not in src, "clean must not run its own git pull"
    # Any refresh must go through the robust shared path, gated on the flag.
    assert "run_self_update" in src
    assert "if refresh:" in src


def test_clean_tears_down_an_untrusted_lab_without_resolving_secrets(tmp_path, monkeypatch):
    """Teardown never waits for trust: a cloned or pre-upgrade lab can still be cleaned,
    and its ??cmd: never runs."""
    from click.testing import CliRunner

    from rodeo.commands import clean as clean_mod

    class FakeLv:
        destroyed: list = []

        def __init__(self, uri):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def domain_xml(self, name):
            return None

        def is_running(self, name):
            return False

        def destroy(self, name):
            FakeLv.destroyed.append(name)

        def undefine(self, name):
            pass

        def list_all_domain_names(self):
            return []

        def net_destroy(self, name):
            pass

        def net_undefine(self, name):
            pass

    marker = tmp_path / "ran"
    lab = tmp_path / "clone"
    lab.mkdir()
    images = tmp_path / "images"
    images.mkdir()
    (lab / "rodeo-plan.yaml").write_text(
        f"name: clone\nstorage:\n  image_dir: {images}\n"
        f"credentials:\n  harvester_os_password: '??cmd:touch {marker} && echo pw'\n"
    )
    monkeypatch.setattr(clean_mod, "is_root", lambda: True)
    monkeypatch.setattr(clean_mod, "LibvirtDriver", FakeLv)
    result = CliRunner().invoke(clean_mod.clean_cmd, ["--yes", "--hard", "--config-dir", str(lab)])
    assert result.exit_code == 0, result.output
    assert "not trusted" in result.output
    assert FakeLv.destroyed
    assert not marker.exists()
