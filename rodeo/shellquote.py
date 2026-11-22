"""Quoting for plan values that end up inside shell scripts.

Plan values (versions, names, URLs, IPs) are data. Every script rodeo sends
to a VM or host goes through bash, so an unquoted value such as
``1.31; touch MARKER`` runs as root there. Use :func:`q` for a value in an
unquoted shell position and :func:`heredoc` for text placed inside a quoted
heredoc (``<<'EOF'``), where quoting would corrupt the content and the only
way out is a line equal to the terminator.

Never put ``q(...)`` inside double quotes: the single quotes it adds become
part of the value. Assign the value to a shell variable instead
(``NAME={q(v)}``) and use ``"$NAME"`` inside double-quoted strings.
"""
from __future__ import annotations

import shlex


def q(value: object) -> str:
    """shlex.quote for any scalar (ints and floats from YAML included)."""
    return shlex.quote(str(value))


def heredoc(body: str, marker: str) -> str:
    """Return *body* for a ``<<'marker'`` heredoc, refusing an early terminator.

    A quoted heredoc expands nothing, so the body is safe unless one of its
    lines equals the terminator: the shell would end the heredoc there and run
    the following lines as commands.
    """
    body = str(body)
    if any(line == marker for line in body.splitlines()):
        raise ValueError(f"a plan value contains the heredoc terminator {marker!r}")
    return body
