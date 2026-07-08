"""Guard against unbalanced quotes in free-form Ansible shell/command tasks.

Ansible parses a free-form `shell:`/`command:` string with split_args, which
counts quotes across the WHOLE block — including comment lines, which it does
not treat as comments. A stray apostrophe (e.g. "opensuse.org's") makes the
single-quote count odd and Ansible aborts with:

    failed at splitting arguments, either an unbalanced jinja2 block or quotes

This mimics split_args' quote tracking and asserts every free-form shell/command
command is balanced, so that class of bug can't ship again. Structured (cmd:)
tasks are exempt — Ansible doesn't split those.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROLES = Path(__file__).resolve().parent.parent / "rodeo" / "data" / "ansible" / "roles"
SHELL_KEYS = {
    "shell", "command",
    "ansible.builtin.shell", "ansible.builtin.command",
    "ansible.legacy.shell", "ansible.legacy.command",
}


def _balanced(cmd: str) -> bool:
    """True if quotes are balanced the way Ansible's split_args sees them.

    A ' toggles single-quote context only outside double quotes (and vice
    versa); a backslash escapes the next char. `#` is NOT a comment here —
    split_args scans the raw string, which is the whole point.
    """
    in_single = in_double = escaped = False
    for ch in cmd:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and not in_single:
            escaped = True
        elif ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
    return not in_single and not in_double


def _free_form_commands():
    for task_file in sorted(ROLES.rglob("*.yml")):
        try:
            docs = yaml.safe_load(task_file.read_text())
        except yaml.YAMLError:
            continue
        if not isinstance(docs, list):
            continue
        for task in docs:
            if not isinstance(task, dict):
                continue
            for key, val in task.items():
                # Only free-form (string) shell/command is split by Ansible;
                # structured {cmd: ...} mappings are exempt.
                if key in SHELL_KEYS and isinstance(val, str):
                    name = task.get("name", "<unnamed>")
                    yield f"{task_file.relative_to(ROLES)} :: {name}", val


@pytest.mark.parametrize("label,cmd", list(_free_form_commands()),
                         ids=lambda x: x if isinstance(x, str) else "")
def test_shell_command_quotes_balanced(label, cmd):
    assert _balanced(cmd), (
        f"Unbalanced quotes in free-form shell/command task [{label}] — "
        "Ansible split_args will abort. Check for a stray apostrophe in a "
        "comment (e.g. \"org's\") or an unclosed quote."
    )
