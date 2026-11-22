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

# Stand-in for sudo: runs the command in sudo's own process group, as sudo
# without use_pty does (FAKE_SUDO_LEADER=1: in a new group the command leads, as
# with use_pty), relays SIGTERM to it, ignores signals the command sends.
# SIBLING_PIDFILE: also start a process in sudo's group that is not the
# command's descendant (the script or `timeout` that launched sudo).
FAKE_SUDO = '''#!{python}
import os, signal, subprocess, sys
SLEEPER = ["{python}", "-c",
           "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"]
if os.environ.get("SIBLING_PIDFILE"):
    sib = subprocess.Popen(SLEEPER)
    with open(os.environ["SIBLING_PIDFILE"], "w") as fh:
        fh.write(str(sib.pid))
leader = os.environ.get("FAKE_SUDO_LEADER") == "1"
child = subprocess.Popen(sys.argv[1:], preexec_fn=os.setpgrp if leader else None)
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


def _kill_pidfile(path: Path) -> None:
    if path.exists():
        try:
            os.kill(int(path.read_text()), signal.SIGKILL)
        except (ProcessLookupError, ValueError):
            pass


def test_non_leader_spares_the_launchers_group(tmp_path, monkeypatch, leader_only_killpg):
    """Without use_pty rodeo shares the launcher's group: a sibling there survives."""
    pidfile, sibfile = tmp_path / "grandchild.pid", tmp_path / "sibling.pid"
    fake_sudo = _write(tmp_path / "sudo", FAKE_SUDO.format(python=sys.executable))
    root = _write(tmp_path / "root-rodeo",
                  ROOT_RODEO.format(python=sys.executable, repo=str(REPO), install=STUB_INSTALL))
    entry = _write(tmp_path / "rodeo", f"#!/bin/sh\nexec {fake_sudo} {root} \"$@\"\n")
    monkeypatch.setattr(mcp_exec, "find_rodeo_bin", lambda: str(entry))
    monkeypatch.setenv("STUB_PIDFILE", str(pidfile))
    monkeypatch.setenv("SIBLING_PIDFILE", str(sibfile))
    monkeypatch.setenv("STOP_GRACE", "1")
    try:
        code, _elapsed, grandchild = _run_cancelled(str(entry), pidfile)
        assert code == mcp_exec.CANCELLED_EXIT
        assert not _alive(grandchild), "rodeo's grandchild outlived the cancel"
        sibling = int(sibfile.read_text())
        assert _alive(sibling), "the root handler killed a process rodeo never started"
    finally:
        _kill_pidfile(pidfile)
        _kill_pidfile(sibfile)


def test_group_leader_stops_a_grandchild_ignoring_sigterm(tmp_path, monkeypatch, leader_only_killpg):
    """use_pty case: rodeo leads its group."""
    pidfile = tmp_path / "grandchild.pid"
    fake_sudo = _write(tmp_path / "sudo", FAKE_SUDO.format(python=sys.executable))
    root = _write(tmp_path / "root-rodeo",
                  ROOT_RODEO.format(python=sys.executable, repo=str(REPO), install=STUB_INSTALL))
    entry = _write(tmp_path / "rodeo", f"#!/bin/sh\nexec {fake_sudo} {root} \"$@\"\n")
    monkeypatch.setattr(mcp_exec, "find_rodeo_bin", lambda: str(entry))
    monkeypatch.setenv("STUB_PIDFILE", str(pidfile))
    monkeypatch.setenv("FAKE_SUDO_LEADER", "1")
    monkeypatch.setenv("STOP_GRACE", "1")
    try:
        code, elapsed, grandchild = _run_cancelled(str(entry), pidfile)
        assert code == mcp_exec.CANCELLED_EXIT
        assert not _alive(grandchild)
        assert elapsed < mcp_exec.KILL_GRACE + 1
    finally:
        _kill_pidfile(pidfile)


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


def test_stop_targets_skip_self_and_parent():
    targets = privilege._stop_targets()
    assert targets is not None
    assert os.getpid() not in targets and os.getppid() not in targets


def _fake_table(monkeypatch, table, *, me, parent, pgrp):
    monkeypatch.setattr(privilege, "_process_table", lambda: table)
    monkeypatch.setattr(privilege.os, "getpid", lambda: me)
    monkeypatch.setattr(privilege.os, "getppid", lambda: parent)
    monkeypatch.setattr(privilege.os, "getpgrp", lambda: pgrp)


def test_stop_targets_non_leader_takes_descendants_only(monkeypatch):
    # 1 script (group 1) -> 2 sudo -> 3 rodeo -> 4 ansible -> 5 ssh; 6 is a sibling in group 1
    table = {1: (0, 1), 2: (1, 1), 3: (2, 1), 4: (3, 1), 5: (4, 1), 6: (1, 1), 7: (1, 7)}
    _fake_table(monkeypatch, table, me=3, parent=2, pgrp=1)
    assert privilege._stop_targets() == [4, 5]


def test_stop_targets_leader_adds_its_group(monkeypatch):
    # rodeo 3 leads group 3; 8 is in group 3 but reparented to init (left rodeo's tree)
    table = {2: (1, 2), 3: (2, 3), 4: (3, 3), 8: (1, 3), 9: (1, 9)}
    _fake_table(monkeypatch, table, me=3, parent=2, pgrp=3)
    assert privilege._stop_targets() == [4, 8]


def test_no_process_table_and_not_leader_signals_nothing(monkeypatch):
    monkeypatch.setattr(privilege, "_process_table", lambda: None)
    monkeypatch.setattr(privilege.os, "getpgrp", lambda: 1)
    calls = []
    monkeypatch.setattr(privilege.os, "killpg", lambda *a: calls.append(a))
    privilege.stop_own_group(grace=0.1)
    assert calls == []


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
