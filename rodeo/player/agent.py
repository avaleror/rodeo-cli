#!/usr/bin/env python3
"""Stand-in for Instruqt's ``agent`` CLI, for chapter scripts only.

Track scripts call ``agent variable set KEY VALUE`` to publish values the
assignment shows through ``[[ Instruqt-Var key="KEY" ... ]]``. The runner
puts a wrapper for this file first on PATH for the scripts it runs, and
points ``RODEO_PLAYER_VARS`` at a root-only JSON file in the lab state
directory. The student shell never gets either.

Standalone on purpose (standard library, no ``rodeo`` imports): the runner
calls it by path, so it works wherever the runner's interpreter is.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ENV_VARS_FILE = "RODEO_PLAYER_VARS"
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
USAGE = "usage: agent variable set KEY VALUE | agent variable get KEY"


def read_vars(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return {str(k): str(v) for k, v in data.items()}


def write_vars(path: Path, values: dict[str, str]) -> None:
    """Atomic replace, mode 0600, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".vars.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(values, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def set_var(path: Path, key: str, value: str) -> None:
    if not _KEY.match(key):
        raise ValueError(f"invalid variable name {key!r}")
    values = read_vars(path)
    values[key] = value
    write_vars(path, values)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    target = os.environ.get(ENV_VARS_FILE)
    if not target:
        print(f"agent: {ENV_VARS_FILE} is not set (only chapter scripts get this agent)", file=sys.stderr)
        return 2
    path = Path(target)
    try:
        if args[:2] == ["variable", "set"] and len(args) == 4:
            set_var(path, args[2], args[3])
            return 0
        if args[:2] == ["variable", "get"] and len(args) == 3:
            values = read_vars(path)
            if args[2] not in values:
                return 1
            print(values[args[2]])
            return 0
    except ValueError as exc:
        print(f"agent: {exc}", file=sys.stderr)
        return 2
    print(f"agent: unsupported command {' '.join(args)!r}\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
