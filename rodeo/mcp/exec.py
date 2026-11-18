"""Execution of rodeo subcommands on behalf of an MCP client."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..privilege import find_rodeo_bin

#: Wall-clock ceiling for one tool call, in seconds. A full deploy is long.
DEFAULT_TIMEOUT = 7200
#: Characters of combined output returned to the client; the tail is kept.
MAX_OUTPUT = 100_000


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


def run_rodeo(
    args: list[str],
    *,
    cwd: Path | str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, str]:
    """Run ``rodeo <args...>`` and return its exit code and combined output.

    ``cwd`` is the lab directory the command runs in (rodeo commands are
    lab-dir relative). A timeout or a missing binary is reported as a non-zero
    exit code with the reason as output, never as an exception.
    """
    try:
        proc = subprocess.run(
            rodeo_argv(args),
            cwd=str(cwd) if cwd else None,
            env=child_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s: rodeo {' '.join(args)}"
    except OSError as exc:
        return 127, f"cannot run rodeo: {exc}"
    return proc.returncode, clamp((proc.stdout or "") + (proc.stderr or ""))
