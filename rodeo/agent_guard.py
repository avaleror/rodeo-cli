"""PreToolUse hook for the Claude Code kit that ``rodeo agent-setup`` writes.

Claude Code runs ``python -I <path>/agent_guard.py [--host H ...]`` before every
Bash tool call and passes the call as JSON on stdin. A command that runs
``rodeo``, ``sudo`` or ``virsh``, or ssh/scp/sftp to a lab host, is blocked
(exit 2) with a message that points the model at the rodeo MCP tools.

This is a mistake filter. It stops a confused model from shelling out around
the MCP server; it is not a boundary: the same user can edit or delete the
settings file that installs it. Standard library only and no package imports:
it runs as a script under ``python -I``, so nothing in the lab directory
(Claude Code's cwd) is ever imported.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

#: Programs the kit keeps out of the agent's shell.
BLOCKED_PROGRAMS = frozenset({"rodeo", "sudo", "virsh"})
#: Programs that reach a host; blocked when an argument names a lab host.
SSH_PROGRAMS = frozenset({"ssh", "scp", "sftp"})
#: Prefixes that run the next word as the command.
WRAPPERS = frozenset({"env", "exec", "nohup", "time", "command", "nice", "stdbuf", "doas", "xargs"})
SHELLS = frozenset({"sh", "bash", "zsh", "dash"})
OPERATORS = frozenset({";", "&&", "||", "|", "&", "(", ")", "|&", ";;", "\n"})

MESSAGE = (
    "Blocked by the rodeo agent kit: {why}. Use the rodeo MCP tools instead "
    "(rodeo_status, rodeo_plan, rodeo_logs, rodeo_preflight, rodeo_deploy, ...); "
    "they run rodeo for you with the right privileges and no prompts. "
    "This is a mistake filter installed by `rodeo agent-setup`."
)

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_FALLBACK = re.compile(r"(?:^|[\s;&|(`$])(rodeo|sudo|virsh)(?=$|[\s;&|)`])")


def _tokens(command: str) -> list[str] | None:
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return None


def _segments(tokens: list[str]) -> list[list[str]]:
    out: list[list[str]] = [[]]
    for tok in tokens:
        if tok in OPERATORS or (tok and set(tok) <= set(";&|()")):
            out.append([])
        else:
            out[-1].append(tok)
    return [s for s in out if s]


def _host_hit(args: list[str], hosts: frozenset[str]) -> str | None:
    for arg in args:
        target = arg.rsplit("@", 1)[-1].split(":", 1)[0]
        if target in hosts:
            return target
    return None


def check(command: str, hosts: frozenset[str] = frozenset(), _depth: int = 0) -> str | None:
    """Why ``command`` is blocked, or None when it may run."""
    tokens = _tokens(command)
    if tokens is None:  # unbalanced quotes: fall back to a plain word match
        m = _FALLBACK.search(command)
        return f"runs {m.group(1)}" if m else None
    for words in _segments(tokens):
        i = 0
        while i < len(words) and (_ASSIGNMENT.match(words[i]) or words[i] in WRAPPERS):
            i += 1
            while i < len(words) and words[i - 1] in WRAPPERS and words[i].startswith("-"):
                i += 1  # wrapper options, e.g. `env -i`, `nice -n 5`
            if i < len(words) and words[i - 1] == "nice" and words[i].lstrip("-").isdigit():
                i += 1
        if i >= len(words):
            continue
        prog = os.path.basename(words[i])
        args = words[i + 1:]
        if prog in BLOCKED_PROGRAMS:
            return f"runs {prog}"
        if prog in SSH_PROGRAMS and (host := _host_hit(args, hosts)):
            return f"{prog} to lab host {host}"
        if prog in SHELLS and "-c" in args and _depth < 3:
            idx = args.index("-c")
            if idx + 1 < len(args) and (why := check(args[idx + 1], hosts, _depth + 1)):
                return why
        # Command substitution inside a word, e.g. echo "$(rodeo status)"
        for arg in words[i:]:
            if ("$(" in arg or "`" in arg) and _depth < 3:
                inner = arg.split("$(", 1)[-1] if "$(" in arg else arg.split("`", 1)[-1]
                if why := check(inner.rstrip(")`"), hosts, _depth + 1):
                    return why
    return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    hosts = frozenset(argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--host")
    try:
        event = json.load(sys.stdin)
        command = event["tool_input"]["command"]
    except (ValueError, KeyError, TypeError):
        return 0  # not a Bash call we understand: let Claude Code decide
    if not isinstance(command, str):
        return 0
    why = check(command, hosts)
    if why is None:
        return 0
    print(MESSAGE.format(why=why), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
