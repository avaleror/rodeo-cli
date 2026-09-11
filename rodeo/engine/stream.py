"""Generic subprocess streaming shared by every engine.

`stream_command` launches a command in its own session (so callers can SIGTERM
the whole process group on cancel), tees each stdout line to the plan log file,
yields it as a `LogLine`, and returns the exit code via the generator return
value:

    rc = yield from stream_command(cmd, log_file, on_proc=self._set_proc)
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Generator

from .events import LogLine


def stream_command(
    cmd: list[str],
    log_file: Path,
    env: dict | None = None,
    on_proc: Callable[[subprocess.Popen | None], None] | None = None,
) -> Generator[LogLine, None, int]:
    """Stream a subprocess's stdout as LogLine events; return its exit code.

    ``on_proc`` (when given) receives the live Popen right after launch and
    ``None`` after it exits — engines use it to expose the current process to
    their ``terminate()``.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
        env=env,
        start_new_session=True,
    )
    if on_proc:
        on_proc(proc)
    with open(log_file, "a", errors="replace") as lf:
        lf.write(f"\n--- {' '.join(cmd)} ---\n")
        for raw in proc.stdout:  # type: ignore[union-attr]
            lf.write(raw)
            yield LogLine(raw.rstrip())
    proc.wait()
    if on_proc:
        on_proc(None)
    return proc.returncode
