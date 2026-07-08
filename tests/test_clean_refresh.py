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
