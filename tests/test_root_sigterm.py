"""WP1b: an MCP cancel reaches the root rodeo behind sudo, and that rodeo stops its group.

The MCP server can only signal processes running as the operator. A write tool
re-execs through sudo, so the root rodeo and its ansible/ssh children are out
of reach; sudo relays SIGTERM (never SIGKILL) to the root rodeo, whose handler
must stop its own group.

Without passwordless sudo only the stub tests run. They simulate the uid wall by
letting the MCP group kill reach the sudo stand-in alone, so a dead grandchild
proves the relay and the handler did the work. The real-sudo test is the only
one that proves it end to end; it is skipped when ``sudo -n true`` fails.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from rodeo import privilege
from rodeo.mcp import exec as mcp_exec

REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs /proc")

# Stand-in for sudo: runs the command in its own process group (sudo without
# use_pty does the same), relays SIGTERM to it, ignores signals the command sends.
FAKE_SUDO = '''#!{python}
import os, signal, subprocess, sys
child = subprocess.Popen(sys.argv[1:])
def relay(sig, frame):
    try:
        if os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
            child.send_signal(sig)
    except ChildProcessError:
        pass
signal.signal(signal.SIGTERM, relay)
sys.exit(child.wait())
'''

# The "root rodeo": install the handler (``install`` decides how), start a
# grandchild that ignores SIGTERM, record its pid, then block like a deploy.
ROOT_RODEO = '''#!{python}
import os, signal, subprocess, sys, time
sys.path.insert(0, {repo!r})
from rodeo import privilege
privilege.ROOT_STOP_GRACE = float(os.environ.get("STOP_GRACE", "3"))
{install}
child = subprocess.Popen(
    [sys.executable, "-c",
     "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"])
pidfile = os.environ["STUB_PIDFILE"]
with open(pidfile + ".tmp", "w") as fh:
    fh.write(str(child.pid))
os.rename(pidfile + ".tmp", pidfile)
child.wait()
'''

STUB_INSTALL = "signal.signal(signal.SIGTERM, privilege._on_sigterm)"
REAL_INSTALL = "assert privilege.install_root_sigterm_handler(), 'handler not installed'"


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def _wait_file(path: Path, within: float = 15.0) -> int:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if path.exists():
            return int(path.read_text())
        time.sleep(0.02)
    raise AssertionError(f"stub never wrote {path}")


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o755)
    return path


def _cancel_when_started(pidfile: Path, cancel: threading.Event, box: dict) -> None:
    box["grandchild"] = _wait_file(pidfile)
    box["t0"] = time.monotonic()
    cancel.set()


def _run_cancelled(entry: str, pidfile: Path) -> tuple[int, float, int]:
    cancel, box = threading.Event(), {}
    t = threading.Thread(target=_cancel_when_started, args=(pidfile, cancel, box), daemon=True)
    t.start()
    code, _ = mcp_exec.run_rodeo(["up"], timeout=60, cancel=cancel)
    elapsed = time.monotonic() - box["t0"]
    t.join(1)
    return code, elapsed, box["grandchild"]


@pytest.fixture
def leader_only_killpg(monkeypatch):
    """The MCP group kill reaches only the group leader (sudo), like across the uid wall."""
    def killpg(pgid, sig):
        os.kill(pgid, sig)
    monkeypatch.setattr(mcp_exec.os, "killpg", killpg)


def test_cancel_through_sudo_stand_in_stops_the_root_group(tmp_path, monkeypatch, leader_only_killpg):
    pidfile = tmp_path / "grandchild.pid"
    fake_sudo = _write(tmp_path / "sudo", FAKE_SUDO.format(python=sys.executable))
    root = _write(tmp_path / "root-rodeo",
                  ROOT_RODEO.format(python=sys.executable, repo=str(REPO), install=STUB_INSTALL))
    entry = _write(tmp_path / "rodeo", f"#!/bin/sh\nexec {fake_sudo} {root} \"$@\"\n")
    monkeypatch.setattr(mcp_exec, "find_rodeo_bin", lambda: str(entry))
    monkeypatch.setenv("STUB_PIDFILE", str(pidfile))
    monkeypatch.setenv("STOP_GRACE", "1")
    try:
        code, elapsed, grandchild = _run_cancelled(str(entry), pidfile)
        assert code == mcp_exec.CANCELLED_EXIT
        assert not _alive(grandchild), "the root rodeo's grandchild outlived the cancel"
        assert elapsed < mcp_exec.KILL_GRACE + 1
    finally:
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_without_the_handler_the_grandchild_survives(tmp_path, monkeypatch, leader_only_killpg):
    """Control: proves the stand-in models the bug, so the test above is not vacuous."""
    pidfile = tmp_path / "grandchild.pid"
    fake_sudo = _write(tmp_path / "sudo", FAKE_SUDO.format(python=sys.executable))
    root = _write(tmp_path / "root-rodeo",
                  ROOT_RODEO.format(python=sys.executable, repo=str(REPO), install="pass"))
    entry = _write(tmp_path / "rodeo", f"#!/bin/sh\nexec {fake_sudo} {root} \"$@\"\n")
    monkeypatch.setattr(mcp_exec, "find_rodeo_bin", lambda: str(entry))
    monkeypatch.setattr(mcp_exec, "KILL_GRACE", 0.5)
    monkeypatch.setenv("STUB_PIDFILE", str(pidfile))
    try:
        _code, _elapsed, grandchild = _run_cancelled(str(entry), pidfile)
        assert _alive(grandchild)
    finally:
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_root_stop_grace_is_inside_kill_grace():
    assert privilege.ROOT_STOP_GRACE + 1 <= mcp_exec.KILL_GRACE


def test_handler_not_installed_without_root(monkeypatch):
    monkeypatch.setattr(privilege, "is_root", lambda: False)
    monkeypatch.setenv("SUDO_USER", "someone")
    before = signal.getsignal(signal.SIGTERM)
    assert privilege.install_root_sigterm_handler() is False
    assert signal.getsignal(signal.SIGTERM) is before


def test_handler_not_installed_when_parent_is_not_sudo(monkeypatch):
    """A root rodeo started by a script must never signal the script's group."""
    monkeypatch.setattr(privilege, "is_root", lambda: True)
    monkeypatch.setenv("SUDO_USER", "someone")
    monkeypatch.setattr(privilege, "_parent_is_sudo", lambda: False)
    before = signal.getsignal(signal.SIGTERM)
    assert privilege.install_root_sigterm_handler() is False
    assert signal.getsignal(signal.SIGTERM) is before


def test_handler_not_installed_for_plain_root(monkeypatch):
    monkeypatch.setattr(privilege, "is_root", lambda: True)
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr(privilege, "_parent_is_sudo", lambda: True)
    assert privilege.install_root_sigterm_handler() is False


def test_handler_installed_for_root_under_sudo(monkeypatch):
    monkeypatch.setattr(privilege, "is_root", lambda: True)
    monkeypatch.setenv("SUDO_USER", "someone")
    monkeypatch.setattr(privilege, "_parent_is_sudo", lambda: True)
    before = signal.getsignal(signal.SIGTERM)
    try:
        assert privilege.install_root_sigterm_handler() is True
        assert signal.getsignal(signal.SIGTERM) is privilege._on_sigterm
    finally:
        signal.signal(signal.SIGTERM, before)


def test_group_members_skip_self_and_parent():
    members = privilege._group_members(os.getpgrp())
    assert members is not None
    assert os.getpid() not in members and os.getppid() not in members


def test_kill_group_tolerates_only_root_members_left(monkeypatch):
    """After sudo exits, killpg from the operator gets EPERM; that must not raise."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    proc.wait()

    def killpg(pgid, sig):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(mcp_exec.os, "killpg", killpg)
    mcp_exec.kill_group(proc, grace=0.1)


def _passwordless_sudo() -> bool:
    if shutil.which("sudo") is None:
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True, check=False).returncode == 0


@pytest.mark.skipif(not _passwordless_sudo(), reason="needs passwordless sudo (sudo -n true)")
def test_cancel_through_real_sudo_stops_the_root_group(tmp_path, monkeypatch):
    """End to end: operator rodeo execs sudo, the root child installs the real handler."""
    pidfile = tmp_path / "grandchild.pid"
    root = _write(tmp_path / "root-rodeo",
                  ROOT_RODEO.format(python=sys.executable, repo=str(REPO), install=REAL_INSTALL))
    # sudo resets the environment, so the pid file path and grace go on the command line.
    entry = _write(tmp_path / "rodeo",
                   f"#!/bin/sh\nexec sudo -n env STUB_PIDFILE={pidfile} STOP_GRACE=1 {root} \"$@\"\n")
    tmp_path.chmod(0o777)
    monkeypatch.setattr(mcp_exec, "find_rodeo_bin", lambda: str(entry))
    try:
        code, elapsed, grandchild = _run_cancelled(str(entry), pidfile)
        assert code == mcp_exec.CANCELLED_EXIT
        deadline = time.monotonic() + 2
        while _alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(grandchild), "root grandchild outlived the cancel through sudo"
        assert elapsed < mcp_exec.KILL_GRACE + 1
    finally:
        if pidfile.exists():
            subprocess.run(["sudo", "-n", "kill", "-9", pidfile.read_text()],
                           capture_output=True, check=False)
