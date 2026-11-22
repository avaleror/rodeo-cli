"""Execution of rodeo subcommands on behalf of an MCP client."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from ..privilege import find_rodeo_bin

#: Wall-clock ceiling for one tool call, in seconds. A full deploy is long.
DEFAULT_TIMEOUT = 7200
#: Characters of combined output returned to the client; the tail is kept.
MAX_OUTPUT = 100_000
#: Seconds between SIGTERM and SIGKILL when a call's process group is stopped.
#: A write tool's root rodeo (behind sudo) gets the SIGTERM through sudo and
#: stops its own group within ``privilege.ROOT_STOP_GRACE``; this must outlast it.
KILL_GRACE = 5.0
#: Seconds between checks for a cancel while a call runs.
POLL_INTERVAL = 0.1
#: Exit code reported for a call the client cancelled (128 + SIGINT, as a shell does).
CANCELLED_EXIT = 130


def rodeo_argv(args: list[str]) -> list[str]:
    """argv for ``rodeo <args...>`` using the absolute path of the rodeo binary."""
    return [find_rodeo_bin(), *args]


def child_env() -> dict[str, str]:
    """Environment for a non-interactive child: no colour, no pager, unbuffered."""
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def clamp(text: str, limit: int = MAX_OUTPUT) -> str:
    """``text`` shortened to ``limit`` characters, keeping the tail (where failures land)."""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    return f"[... {dropped} characters omitted ...]\n{text[-limit:]}"


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_group(proc: subprocess.Popen, grace: float | None = None) -> None:
    """SIGTERM the process group led by ``proc``, then SIGKILL it after ``grace`` seconds.

    ``proc`` was started with ``start_new_session=True``, so its pid is the
    group id and ``ansible-playbook``/``ssh`` grandchildren are in the group.
    """
    grace = KILL_GRACE if grace is None else grace
    pgid = proc.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        # PermissionError: only root members are left (sudo already exited);
        # the root rodeo stops those itself.
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        proc.poll()  # reap the leader so its zombie does not keep the group "alive"
        if not _group_alive(pgid):
            return
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_rodeo(
    args: list[str],
    *,
    cwd: Path | str | None = None,
    timeout: int | float = DEFAULT_TIMEOUT,
    cancel: threading.Event | None = None,
) -> tuple[int, str]:
    """Run ``rodeo <args...>`` and return its exit code and combined output.

    ``cwd`` is the lab directory the command runs in (rodeo commands are
    lab-dir relative). A timeout, a cancel or a missing binary is reported as a
    non-zero exit code with the reason as output, never as an exception. On a
    timeout or when ``cancel`` is set, the whole process group is killed, not
    only the ``rodeo`` child.
    """
    try:
        proc = subprocess.Popen(
            rodeo_argv(args),
            cwd=str(cwd) if cwd else None,
            env=child_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except OSError as exc:
        return 127, f"cannot run rodeo: {exc}"

    deadline = time.monotonic() + timeout
    stopped: tuple[int, str] | None = None
    while True:
        slice_ = min(POLL_INTERVAL, max(deadline - time.monotonic(), 0.0))
        try:
            out, err = proc.communicate(timeout=slice_)
            break
        except subprocess.TimeoutExpired:
            pass
        if cancel is not None and cancel.is_set():
            stopped = (CANCELLED_EXIT, f"cancelled: rodeo {' '.join(args)}")
        elif time.monotonic() >= deadline:
            stopped = (124, f"timed out after {timeout}s: rodeo {' '.join(args)}")
        if stopped:
            kill_group(proc)
            try:  # drain the pipe; a grandchild that left the group may still hold it
                proc.communicate(timeout=KILL_GRACE)
            except subprocess.TimeoutExpired:
                proc.kill()
            return stopped
    return proc.returncode, clamp((out or "") + (err or ""))
