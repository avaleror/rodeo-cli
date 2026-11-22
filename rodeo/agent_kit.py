"""The Claude Code kit ``rodeo agent-setup`` writes into a lab directory.

A mistake filter, not a boundary: project settings that deny ``rodeo``,
``sudo``, ``virsh`` and ssh to the lab's hosts in the agent's shell, plus a
PreToolUse hook (``rodeo/agent_guard.py``) that sends the model to the rodeo
MCP tools. The same user can edit or delete the file, so it stops a confused
model, not a hostile one.

The merge only adds: the user's rules and hooks are kept as they are, and a
second run changes nothing unless the lab's hosts or rodeo's interpreter moved
(then rodeo's own hook entry is updated in place).
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from .config import ConfigError

SETTINGS_REL = Path(".claude") / "settings.json"
#: Marks rodeo's own hook entry in the settings (the guard script's file name).
GUARD_MARK = "agent_guard.py"
BASE_DENY = ("Bash(rodeo)", "Bash(rodeo *)", "Bash(sudo *)", "Bash(virsh *)")
#: MCP clients that have no kit yet, with the config file that shows they are set up.
CLIENTS_WITHOUT_KIT = {
    "Gemini CLI": (".gemini/settings.json",),
    "Codex CLI": (".codex/config.toml",),
}

# Hosts end up inside a shell command (the hook) and permission rules, so only
# plain host names and IP addresses are used; anything else is skipped.
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,252}$")


def lab_hosts(lab: Path) -> list[str]:
    """VM names and addresses, the VIPs, and the AWS host of the lab's plan.

    Best effort: a plan that does not load gives no hosts (the base rules still
    apply). Nothing resolves: the plan is read with ``secrets="off"``.
    """
    plan = lab / "rodeo-plan.yaml"
    if not plan.is_file():
        return []
    from .config import load_config

    try:
        cfg = load_config(plan, secrets="off")
    except (ConfigError, OSError, ValueError):
        return []
    found: list[str] = []
    vms = cfg.get("vms")
    if isinstance(vms, dict):
        for name, spec in vms.items():
            found.append(str(name))
            if isinstance(spec, dict) and spec.get("ip"):
                found.append(str(spec["ip"]))
    net = cfg.get("network")
    if isinstance(net, dict):
        found += [str(net[k]) for k in ("vip", "rancher_ip") if net.get(k)]
    if cfg.get("name"):
        try:
            from .providers.remote_up import load_aws_host_state

            state = load_aws_host_state(str(cfg["name"])) or {}
            found += [str(state[k]) for k in ("public_ip", "ssh") if state.get(k)]
        except Exception:  # noqa: BLE001 - a broken state file must not stop the kit
            pass
    out: list[str] = []
    for h in found:
        h = h.rsplit("@", 1)[-1]
        if _HOST.match(h) and h not in out:
            out.append(h)
    return out


def deny_rules(hosts: list[str]) -> list[str]:
    rules = list(BASE_DENY)
    for h in hosts:
        rules += [f"Bash(ssh *{h}*)", f"Bash(scp *{h}*)"]
    return rules


def hook_command(hosts: list[str]) -> str:
    """The hook's shell command. ``-I`` keeps the lab directory (Claude Code's
    cwd) off ``sys.path``, so a ``json.py`` planted in a lab is never imported;
    the guard is a stdlib-only script, so it needs nothing else on the path."""
    from . import agent_guard

    args = [sys.executable, "-I", str(Path(agent_guard.__file__).resolve())]
    for h in hosts:
        args += ["--host", h]
    return " ".join(shlex.quote(a) for a in args)


def _is_ours(entry: Any) -> bool:
    return isinstance(entry, dict) and any(
        isinstance(h, dict) and GUARD_MARK in str(h.get("command", ""))
        for h in entry.get("hooks") or []
    )


def _hook_runnable(entry: dict[str, Any]) -> bool:
    """Interpreter and guard script of rodeo's hook entry still exist (rodeo moved?)."""
    for h in entry.get("hooks") or []:
        try:
            argv = shlex.split(str(h.get("command", "")))
        except ValueError:
            return False
        if len(argv) < 3 or not (Path(argv[0]).exists() and Path(argv[2]).exists()):
            return False
    return True


def _read(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ConfigError(f"{path} is a symlink: rodeo does not write through links in a lab")
    if not path.exists():
        return {}
    text = path.read_text()
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ConfigError(f"{path} is not valid JSON ({exc}); fix it, rodeo will not overwrite it")
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must hold a JSON object; rodeo will not overwrite it")
    return data


def _child(parent: dict[str, Any], key: str, kind: type, where: str) -> Any:
    value = parent.setdefault(key, kind())
    if not isinstance(value, kind):
        raise ConfigError(f"{where}.{key} must be a JSON {kind.__name__}; rodeo will not overwrite it")
    return value


def merge(settings: dict[str, Any], hosts: list[str]) -> dict[str, Any]:
    """``settings`` with the kit added. Existing rules and hooks are kept."""
    deny = _child(_child(settings, "permissions", dict, "settings"), "deny", list, "permissions")
    for rule in deny_rules(hosts):
        if rule not in deny:
            deny.append(rule)
    pre = _child(_child(settings, "hooks", dict, "settings"), "PreToolUse", list, "hooks")
    entry = {"matcher": "Bash", "hooks": [{"type": "command", "command": hook_command(hosts)}]}
    for i, existing in enumerate(pre):
        if _is_ours(existing):
            pre[i] = entry
            break
    else:
        pre.append(entry)
    return settings


def install(lab: Path) -> tuple[Path, bool]:
    """Write the kit into ``lab``. Returns the settings path and whether it changed."""
    from .labfs import write_lab_file

    path = lab / SETTINGS_REL
    current = _read(path)
    before = json.dumps(current, sort_keys=True)
    merged = merge(json.loads(before), lab_hosts(lab))
    if json.dumps(merged, sort_keys=True) == before:
        return path, False
    write_lab_file(lab, SETTINGS_REL, json.dumps(merged, indent=2) + "\n", 0o644)
    return path, True


def status(lab: Path) -> dict[str, Any]:
    """Whether the Claude Code kit is in ``lab``, and which other clients have no kit."""
    path = lab / SETTINGS_REL
    try:
        settings = _read(path)
        deny = (settings.get("permissions") or {}).get("deny") or []
        pre = (settings.get("hooks") or {}).get("PreToolUse") or []
        ours = [e for e in pre if _is_ours(e)]
        present = all(r in deny for r in BASE_DENY) and bool(ours)
        problem = None
        if present and not all(_hook_runnable(e) for e in ours):
            present = False
            problem = "the hook points at a Python or script that is gone; re-run rodeo agent-setup"
    except (ConfigError, OSError, AttributeError, TypeError) as exc:
        present, problem = False, str(exc)
    home = Path.home()
    without = [
        name for name, rels in CLIENTS_WITHOUT_KIT.items()
        if any((lab / r).exists() or (home / r).exists() for r in rels)
    ]
    return {
        "lab": str(lab),
        "settings": str(path),
        "claude_code": present,
        "problem": problem,
        "clients_without_kit": without,
    }
