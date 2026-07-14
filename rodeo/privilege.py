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
import sys
from pathlib import Path


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
        return
    relaunch_as_root(argv)


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
