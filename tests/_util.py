"""Small helpers shared across test modules."""
from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def plain_output(text: str) -> str:
    """Strip Rich/ANSI markup so assertions work across Python versions."""
    return _ANSI_RE.sub("", text)