"""rodeo.i18n: read-only lookup backed by the optional multilang package.

multilang isn't a runtime dependency (see the `i18n` extra in pyproject.toml),
so the primary contract to lock in is graceful degradation: no multilang
installed, or no database yet, must return None rather than raise.
"""
from __future__ import annotations

import builtins

from rodeo import i18n


def test_get_string_returns_none_without_multilang_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "i18n_db_path", lambda: tmp_path / "i18n.db")

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "multilang":
            raise ImportError("no module named multilang")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    assert i18n.get_string("welcome.title", "es") is None


def test_get_string_returns_none_when_db_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "i18n_db_path", lambda: tmp_path / "does-not-exist.db")
    assert i18n.get_string("welcome.title", "es") is None
