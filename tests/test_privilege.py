"""Root escalation: re-exec trigger + the ownership-fixup atexit hook."""
from __future__ import annotations

import atexit

from rodeo import privilege


def test_ensure_root_relaunches_when_not_root(monkeypatch):
    monkeypatch.setattr(privilege, "is_root", lambda: False)
    calls = []
    monkeypatch.setattr(privilege, "relaunch_as_root", lambda argv: calls.append(argv))
    privilege.ensure_root(["deploy", "--no-tui"])
    assert calls == [["deploy", "--no-tui"]]


def test_ensure_root_registers_ownership_fixup_when_escalated(monkeypatch):
    """Already root + SUDO_USER set (the re-exec'd child, or a manual `sudo rodeo`)
    must register the atexit hook so ~/.rodeo isn't left root-owned."""
    monkeypatch.setattr(privilege, "is_root", lambda: True)
    monkeypatch.setenv("SUDO_USER", "avalero")
    registered = []
    monkeypatch.setattr(atexit, "register", lambda fn: registered.append(fn))
    privilege.ensure_root(["deploy"])
    from rodeo.paths import fix_invoking_ownership

    assert fix_invoking_ownership in registered


def test_ensure_root_no_fixup_when_genuinely_root_no_sudo_user(monkeypatch):
    """Plain root with no invoking user (no SUDO_USER) — nothing to hand back."""
    monkeypatch.setattr(privilege, "is_root", lambda: True)
    monkeypatch.delenv("SUDO_USER", raising=False)
    registered = []
    monkeypatch.setattr(atexit, "register", lambda fn: registered.append(fn))
    privilege.ensure_root(["deploy"])
    assert registered == []
