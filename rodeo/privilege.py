"""Root escalation and session persistence without the ``sudo -E`` dance.

`rodeo up` needs root for the privileged deploy phases (kvm_host, libvirt). Rather
than make a beginner remember ``sudo -E`` and ``export RODEO=…``, the command
re-executes itself under sudo. Because secrets live in ~/.rodeo/secrets.yaml (file
form, resolved by config.py), no environment needs to be forwarded.

tmux wrapping: on long deploys (Harvester takes 1-2 h) an Instruqt or SSH session
dropping mid-deploy kills the process. ``ensure_tmux_session`` re-execs the current
command inside a named tmux session so the deploy survives any disconnect. The caller
can re-attach at any time with ``tmux attach -t <session>``.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

#: Seconds the root rodeo waits after SIGTERM before it SIGKILLs what is left
#: of the processes it started. Must stay below ``mcp.exec.KILL_GRACE``: after that the
#: MCP server SIGKILLs sudo, and sudo cannot relay SIGKILL to a root process.
ROOT_STOP_GRACE = 3.0


def is_root() -> bool:
    return os.geteuid() == 0


def find_rodeo_bin() -> str:
    """Locate the rodeo entry point to re-exec (same pattern as bootstrap)."""
    return shutil.which("rodeo") or os.path.abspath(sys.argv[0])


def relaunch_as_root(argv: list[str]) -> None:
    """Replace this process with ``sudo <rodeo> <argv...>``. Does not return on success.

    No ``-E``: the child reads ~/.rodeo/secrets.yaml directly, so the parent
    environment is irrelevant.
    """
    if shutil.which("sudo") is None:
        raise RuntimeError(
            "This step needs root and 'sudo' was not found. "
            "Re-run as root, e.g. 'su -' then the same command."
        )
    rodeo = find_rodeo_bin()
    os.execvp("sudo", ["sudo", rodeo, *argv])  # noqa: S606 — intentional re-exec


def ensure_root(argv: list[str]) -> None:
    """If not already root, re-exec under sudo with ``argv``. Returns only when root.

    When this *is* the escalated (or manually ``sudo``'d) root process, register
    an atexit hook to hand ``~/.rodeo`` back to the invoking user on the way out —
    otherwise every file this run writes stays root-owned and read-only commands
    need ``sudo`` again afterward. See :func:`paths.fix_invoking_ownership`.
    """
    if is_root():
        if os.environ.get("SUDO_USER"):
            import atexit

            from .paths import fix_invoking_ownership

            atexit.register(fix_invoking_ownership)
            install_root_sigterm_handler()
        return
    relaunch_as_root(argv)


def _parent_is_sudo() -> bool:
    """True when our direct parent is a sudo process (the relay, or its pty monitor)."""
    ppid = os.getppid()
    try:
        name = Path(f"/proc/{ppid}/comm").read_text().strip()
    except OSError:
        try:
            name = subprocess.run(
                ["ps", "-o", "comm=", "-p", str(ppid)],
                capture_output=True, text=True, timeout=5, check=False,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return False
    return os.path.basename(name) == "sudo"


def _process_table() -> dict[int, tuple[int, int]] | None:
    """``{pid: (ppid, pgid)}`` for live (non-zombie) processes, or None if unknown.

    Read from ``/proc`` on Linux, else from ``ps`` (macOS, BSD).
    """
    table: dict[int, tuple[int, int]] = {}
    proc = Path("/proc")
    if (proc / "self" / "stat").exists():
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text()
            except OSError:
                continue
            # Fields after the ")" that closes comm: state ppid pgrp ...
            fields = stat.rsplit(")", 1)[-1].split()
            if len(fields) > 2 and fields[0] != "Z":
                table[int(entry.name)] = (int(fields[1]), int(fields[2]))
        return table
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,pgid=,stat="],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3][:1] != "Z" and parts[0].isdigit():
            table[int(parts[0])] = (int(parts[1]), int(parts[2]))
    return table or None


def _stop_targets() -> list[int] | None:
    """Pids the root rodeo may stop: its own descendants, plus its whole process
    group only when it leads that group (sudo's ``use_pty`` case).

    Without ``use_pty`` rodeo inherits the launcher's group (a script, ``timeout``,
    a CI step), so signalling the group would hit processes rodeo never started.
    This process and its parent (sudo) are never included. None when the process
    table cannot be read.
    """
    table = _process_table()
    if table is None:
        return None
    me, parent = os.getpid(), os.getppid()
    children: dict[int, list[int]] = {}
    for pid, (ppid, _pgid) in table.items():
        children.setdefault(ppid, []).append(pid)
    found: set[int] = set()
    todo = [me]
    while todo:
        for kid in children.get(todo.pop(), []):
            if kid not in found:
                found.add(kid)
                todo.append(kid)
    pgid = os.getpgrp()
    if pgid == me:
        found |= {pid for pid, (_ppid, g) in table.items() if g == pgid}
    found -= {me, parent}
    return sorted(found)


def _signal_all(pids: list[int], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def _reap_children() -> None:
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def stop_own_group(grace: float | None = None) -> None:
    """SIGTERM what this rodeo started (ansible, ssh), then SIGKILL survivors after ``grace``.

    Targets are re-read on every poll so children started meanwhile are caught.
    This process and its parent (sudo) are spared: sudo ignores signals its own
    command sends, and this process still has to exit cleanly.
    """
    grace = ROOT_STOP_GRACE if grace is None else grace
    targets = _stop_targets()
    if targets is None:  # no process table: only a group we lead is safe to signal
        if os.getpgrp() == os.getpid():
            try:
                os.killpg(os.getpgrp(), signal.SIGTERM)
            except OSError:
                pass
        return
    _signal_all(targets, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while targets and time.monotonic() < deadline:
        time.sleep(0.05)
        _reap_children()
        fresh = _stop_targets() or []
        _signal_all([p for p in fresh if p not in targets], signal.SIGTERM)
        targets = fresh
    _signal_all(targets, signal.SIGKILL)
    _reap_children()


def _on_sigterm(signum, frame) -> None:  # noqa: ARG001 - signal handler signature
    signal.signal(signal.SIGTERM, signal.SIG_IGN)  # a second TERM must not re-enter
    stop_own_group()
    from .paths import fix_invoking_ownership

    fix_invoking_ownership()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(128 + signal.SIGTERM)


def install_root_sigterm_handler() -> bool:
    """On SIGTERM, stop what this root rodeo started and exit. True when installed.

    An MCP cancel or timeout signals the group it started, but the escalated
    rodeo and its ansible/ssh children run as uid 0 (and, with sudo's
    ``use_pty``, in another session), so only sudo receives the signal. sudo
    relays SIGTERM to this process (it cannot relay SIGKILL). Without this
    handler the children would outlive the cancel.

    Installed only in a root process whose direct parent is sudo. Even then
    the handler stops only rodeo's own descendants, plus its process group
    when rodeo leads it (``use_pty``): without ``use_pty`` rodeo shares the
    launcher's group (a script, ``timeout``, a CI step), which it must not touch.
    """
    if not is_root() or not os.environ.get("SUDO_USER") or not _parent_is_sudo():
        return False
    signal.signal(signal.SIGTERM, _on_sigterm)
    return True


def sudo_prefix() -> list[str]:
    """['sudo'] when escalation is needed and possible, else []. For one-off commands."""
    if is_root() or shutil.which("sudo") is None:
        return []
    return ["sudo"]


def in_tmux() -> bool:
    """True when the current process is already running inside a tmux session."""
    return bool(os.environ.get("TMUX"))


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def ensure_tmux_session(session_name: str, argv: list[str] | None = None) -> None:
    """If not already in tmux, re-exec inside a named session. Does not return on success.

    The full command (``sys.argv`` or ``argv``) is run inside the session so the
    deploy survives SSH/Instruqt disconnects. The caller's terminal gets a short
    message with the re-attach command and then the session is created in the
    foreground (the user sees it immediately and can detach with Ctrl+b d).

    When tmux is not installed the function returns silently — the caller continues
    without session protection and should warn the user.
    """
    if in_tmux() or not tmux_available():
        return

    cmd = argv if argv is not None else sys.argv[:]
    # Quote each argument safely for the shell command tmux will run.
    import shlex
    shell_cmd = " ".join(shlex.quote(str(a)) for a in cmd)

    # If a session with this name already exists, attach to it instead of
    # starting a new deploy — the previous one may still be running.
    result = os.system(f"tmux has-session -t {shlex.quote(session_name)} 2>/dev/null")  # noqa: S605
    if result == 0:
        print(
            f"\n[tmux] Session '{session_name}' already exists.\n"
            f"  Re-attach (as the user who started it, no sudo):\n"
            f"    tmux attach -t {session_name}\n"
            f"  Start fresh: tmux kill-session -t {session_name} && rodeo up\n"
        )
        os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])  # noqa: S606

    print(
        f"\n[tmux] Starting session '{session_name}' — your deploy will survive disconnects.\n"
        f"  Detach any time:  Ctrl+b  d\n"
        f"  Re-attach (as the user who started rodeo up, no sudo):\n"
        f"    tmux attach -t {session_name}\n"
    )
    # Keep the window open after the command exits so errors are readable.
    # new-session -A: attach if exists (race safety), run the full command
    os.execvp(  # noqa: S606
        "tmux",
        ["tmux", "new-session", "-A", "-s", session_name,
         f"{shell_cmd}; echo; echo '[rodeo] done — press any key to close'; read -r _"],
    )


# Re-exported for callers that build their own relaunch argv.
def home_of_invoking_user() -> Path:
    """Best-effort real user's home even under sudo (for ~/.rodeo locations)."""
    from .paths import invoking_home

    return invoking_home()
