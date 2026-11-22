"""Declarative MCP tool surface over the rodeo CLI.

One entry per tool: the rodeo subcommand it runs, its typed parameters, and
whether it changes state (``mutating``). Mutating tools are only offered when
the server runs with writes enabled. Adding a tool means adding a row here —
no protocol or dispatch code changes.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .exec import run_rodeo

#: Lab directory parameter, shared by every lab-scoped tool.
LAB_DIR_DESC = (
    "Lab directory to run in (the one holding rodeo-plan.yaml). "
    "Defaults to the server's --lab-dir, else its working directory."
)


@dataclass(frozen=True)
class Arg:
    """One tool parameter and how it reaches the rodeo command line.

    ``kind`` is ``flag`` (boolean, emits ``spec`` when true), ``option``
    (emits ``spec`` then the value), ``positional`` (emits the value) or
    ``repeat`` (array, emits ``spec`` then a value per item).

    ``path`` marks values the lab allowlist checks: ``"path"`` for a file or
    directory, ``"profile"`` for a profile reference that may also be a URL.
    """

    name: str
    kind: str
    description: str
    spec: str = ""
    type: str = "string"
    enum: tuple[str, ...] = ()
    required: bool = False
    path: str = ""

    def schema(self) -> dict:
        prop: dict = {"type": self.type, "description": self.description}
        if self.kind == "repeat":
            prop = {"type": "array", "items": {"type": "string"},
                    "description": self.description}
        if self.enum:
            prop["enum"] = list(self.enum)
        return prop

    def render(self, value) -> list[str]:
        """The argv fragment this parameter contributes for ``value``."""
        if value is None:
            return []
        if self.kind == "flag":
            return [self.spec] if value else []
        if self.kind == "positional":
            return [str(value)]
        if self.kind == "repeat":
            out: list[str] = []
            for item in value:
                out += [self.spec, str(item)]
            return out
        return [self.spec, str(value)]


@dataclass(frozen=True)
class Tool:
    """An MCP tool. Either runs ``rodeo <command> <fixed> <args>`` or a local handler."""

    name: str
    description: str
    command: tuple[str, ...] = ()
    args: tuple[Arg, ...] = ()
    fixed: tuple[str, ...] = ()
    mutating: bool = False
    lab_scoped: bool = False
    handler: Callable[[dict], str] | None = None
    timeout: int | None = None

    def input_schema(self) -> dict:
        props = {a.name: a.schema() for a in self.args}
        if self.lab_scoped:
            props["lab_dir"] = {"type": "string", "description": LAB_DIR_DESC}
        required = [a.name for a in self.args if a.required]
        schema: dict = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return schema

    def argv(self, params: dict) -> list[str]:
        """Full rodeo argv for ``params``: command, parameters in declaration
        order, then the fixed flags. Fixed flags come last so a positional
        (``rodeo help <command>``) still precedes them."""
        out = list(self.command)
        for arg in self.args:
            out += arg.render(params.get(arg.name))
        return out + list(self.fixed)

    def descriptor(self) -> dict:
        suffix = "" if not self.mutating else " CHANGES STATE."
        return {
            "name": self.name,
            "description": self.description + suffix,
            "inputSchema": self.input_schema(),
        }


# --- Local handlers (no subprocess) ----------------------------------------

def _engines(_params: dict) -> str:
    """Every registered lab type, native phase pipelines and external engines."""
    from ..profiles import registered_profiles

    rows = []
    for profile in registered_profiles():
        builder = profile.builder or {}
        rows.append({
            "type": profile.name,
            "engine": "external" if builder.get("external") else "native",
            "phases": list(getattr(profile, "phases", []) or []),
            "native_logins": bool(getattr(profile, "native_logins", True)),
            "title": builder.get("title") or profile.name,
            "note": builder.get("note") or "",
        })
    rows.sort(key=lambda r: (r["engine"], r["type"]))
    return json.dumps(rows, indent=2)


# --- Parameters reused by several tools ------------------------------------

_OUTPUT = Arg("output", "option", "Output format.", "--output",
              enum=("text", "json"))
_VM = Arg("vm", "positional", "VM name.", required=True)
_PARAM = Arg("param", "repeat", "Plan overrides as KEY=VALUE.", "--param")


READ_TOOLS: tuple[Tool, ...] = (
    Tool("rodeo_version", "rodeo version.", ("--version",)),
    Tool("rodeo_help",
         "Help for rodeo or one of its subcommands — use it to discover any "
         "command this server does not expose as its own tool.",
         (),
         (Arg("command", "positional",
              "Subcommand to describe, e.g. 'fleet' or 'up'. Omit for the top level."),),
         fixed=("--help",)),
    Tool("rodeo_engines",
         "Registered lab types with their engine kind: native (rodeo's own phase "
         "pipeline) or external (lab-in-a-box and plugin-provided engines). JSON.",
         handler=_engines),
    Tool("rodeo_doctor",
         "Host readiness report and which lab fits this host.",
         ("doctor",), (_OUTPUT,)),
    Tool("rodeo_profiles", "List bundled, custom and catalog profiles.", ("profiles",)),
    Tool("rodeo_plan",
         "Resolve the lab plan and show what a deploy would do. Read-only.",
         ("plan",), (_PARAM,), lab_scoped=True),
    Tool("rodeo_status",
         "VM states and cluster reachability for the lab.",
         ("status",), (_OUTPUT, _PARAM), lab_scoped=True),
    Tool("rodeo_list", "List rodeo VMs known to libvirt on this host.", ("list",),
         (Arg("uri", "option", "Libvirt connection URI.", "--uri"),)),
    Tool("rodeo_logs",
         "Last lines of a VM's serial log (never follows).",
         ("logs",),
         (_VM, Arg("lines", "option", "Lines to show.", "--lines", type="integer")),
         fixed=("--no-follow",), lab_scoped=True),
    Tool("rodeo_instances_list", "List multi-instance labs.", ("instances", "list"),
         (Arg("dir", "option", "Where the labs are.", "--dir", path="path"),)),
    Tool("rodeo_fleet_status",
         "Run status on every host of a fleet inventory.",
         ("fleet", "status"),
         (Arg("file", "option", "Fleet inventory file.", "--file", required=True, path="path"),
          _OUTPUT,
          Arg("label", "repeat", "Only hosts with these labels.", "--label"),
          Arg("host", "repeat", "Only these host ids.", "--host")),
         timeout=900),
    Tool("rodeo_fleet_doctor",
         "Reachability and readiness of every host of a fleet inventory.",
         ("fleet", "doctor"),
         (Arg("file", "option", "Fleet inventory file.", "--file", required=True, path="path"), _OUTPUT),
         timeout=900),
    Tool("rodeo_preflight",
         "Deploy preflight only: validate the plan and the host, deploy nothing.",
         ("deploy",), (_PARAM,), fixed=("--check", "--no-tui"), lab_scoped=True,
         timeout=900),
)


WRITE_TOOLS: tuple[Tool, ...] = (
    Tool("rodeo_up",
         "Front door: set up the lab directory, secrets and deploy it. Works for "
         "native and external (lab-in-a-box) engines. Long-running.",
         ("up",),
         (Arg("profile", "option", "Profile to deploy.", "--profile"),
          Arg("name", "option", "Lab name.", "--name"),
          Arg("dir", "option", "Lab directory to create or use.", "--dir", path="path"),
          Arg("no_deploy", "flag", "Seed the lab and stop before deploying.", "--no-deploy")),
         fixed=("--yes", "--no-tmux"), mutating=True, lab_scoped=True),
    Tool("rodeo_deploy",
         "Run the phase pipeline for the lab in place (no TUI).",
         ("deploy",),
         (Arg("from_phase", "option",
              "Resume from this phase, e.g. 'rancher' or 'labinabox'.", "--from"),
          Arg("force", "flag", "Ignore cached phase completion.", "--force"),
          Arg("finalise", "flag", "Include guarded finalise phases.", "--finalise"),
          _PARAM),
         fixed=("--no-tui",), mutating=True, lab_scoped=True),
    Tool("rodeo_init",
         "Seed a lab directory (plan + definition) for a profile.",
         ("init",),
         (Arg("profile", "option", "Profile to seed from.", "--profile"),
          Arg("target_dir", "positional", "Directory to seed. Default: the lab dir.",
              path="path"),
          Arg("force", "flag", "Overwrite existing files.", "--force")),
         mutating=True, lab_scoped=True),
    Tool("rodeo_new",
         "Create a custom profile from a bundled one, a catalog URL, a zip or a lab JSON.",
         ("new",),
         (Arg("name", "positional", "New profile name.", required=True),
          Arg("from_base", "option",
              "Profile to start from: a bundled name or an https:// catalog URL. "
              "Once a lab list exists (rodeo lab add), bundled names only.",
              "--from", path="profile"),
          Arg("from_zip", "option", "Profile zip to import.", "--from-zip", path="path"),
          Arg("from_lab_json", "option", "lab-in-a-box lab JSON to import.", "--from-lab-json",
              path="path"),
          Arg("force", "flag", "Overwrite an existing custom profile.", "--force")),
         mutating=True),
    Tool("rodeo_start", "Start the lab's VMs.", ("start",), (_PARAM,),
         fixed=("--yes",), mutating=True, lab_scoped=True),
    Tool("rodeo_stop", "Stop the lab's VMs.", ("stop",), (_PARAM,),
         fixed=("--yes",), mutating=True, lab_scoped=True),
    Tool("rodeo_start_if_needed", "Start the lab's VMs only if they are down.",
         ("start-if-needed",), mutating=True, lab_scoped=True),
    Tool("rodeo_restart", "Restart one VM.", ("restart",),
         (_VM, Arg("hard", "flag", "Force-kill instead of ACPI shutdown.", "--hard")),
         mutating=True, lab_scoped=True),
    Tool("rodeo_clean",
         "Destroy the lab's VMs and artifacts. DESTRUCTIVE — 'all' resets the host.",
         ("clean",),
         (Arg("all", "flag", "Host reset: every rodeo VM, network and artifact.", "--all"),
          Arg("secrets", "flag", "Also remove ~/.rodeo/secrets.yaml.", "--secrets"),
          Arg("hard", "flag", "Skip the graceful stop.", "--hard")),
         fixed=("--yes",), mutating=True, lab_scoped=True),
    Tool("rodeo_destroy",
         "Destroy the lab's cloud resources. DESTRUCTIVE.",
         ("destroy",),
         (Arg("cloud", "flag", "Destroy the cloud instance too.", "--cloud"),),
         fixed=("--yes",), mutating=True, lab_scoped=True),
    Tool("rodeo_set_password", "Set the lab's UI passwords.", ("set-password",),
         (Arg("target", "option", "Which UI.", "--target",
              enum=("both", "harvester", "rancher")),),
         fixed=("--yes",), mutating=True, lab_scoped=True),
    Tool("rodeo_install_extensions", "Install the Rancher UI extensions the plan pins.",
         ("install-extensions",), fixed=("--yes",), mutating=True, lab_scoped=True),
    Tool("rodeo_self_update", "Update rodeo-cli from upstream.", ("self-update",),
         (Arg("branch", "option", "Branch to update from.", "--branch"),
          Arg("force", "flag", "Discard local changes.", "--force")),
         mutating=True),
)


_LAB_MARKERS = ("rodeo-plan.yaml", "definition.yaml")
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PLUGIN_TOOLS: dict[str, Tool] = {}


def builtin_names() -> frozenset[str]:
    return frozenset(t.name for t in READ_TOOLS + WRITE_TOOLS)


def register_mcp_tool(tool: Tool, *, mutating: bool, lab_scoped: bool) -> None:
    """Add a plugin tool next to the built-in table.

    ``mutating`` and ``lab_scoped`` must be stated (they override the Tool's
    own fields). A mutating tool is offered only with ``--allow-write``; every
    tool goes through the same lab allowlist (``lab_dir`` when lab-scoped, and
    each ``Arg`` with ``path`` set). A built-in name, or a name another plugin
    took, is refused with ValueError: a plugin can't replace a built-in tool.
    """
    if not isinstance(tool, Tool):
        raise TypeError("register_mcp_tool needs a rodeo.mcp.tools.Tool")
    if not isinstance(mutating, bool) or not isinstance(lab_scoped, bool):
        raise TypeError("mutating and lab_scoped must be True or False")
    if not _TOOL_NAME_RE.match(tool.name):
        raise ValueError(f"MCP tool name {tool.name!r} must match {_TOOL_NAME_RE.pattern}")
    if tool.name in builtin_names():
        raise ValueError(f"MCP tool {tool.name!r} is built in: a plugin can't override it")
    if tool.name in _PLUGIN_TOOLS:
        raise ValueError(f"MCP tool {tool.name!r} is already registered by a plugin")
    if any(a.name == "lab_dir" for a in tool.args):
        raise ValueError("lab_dir is reserved: declare lab_scoped=True instead")
    _PLUGIN_TOOLS[tool.name] = replace(tool, mutating=mutating, lab_scoped=lab_scoped)


def tool_map(*, allow_write: bool) -> dict[str, Tool]:
    """Offered tools by name: read-only always, lifecycle only with writes enabled.
    Plugin tools follow the same rule and never replace a built-in."""
    from ..plugins import load_plugins

    load_plugins()
    tools = list(READ_TOOLS) + (list(WRITE_TOOLS) if allow_write else [])
    out = {t.name: t for t in tools}
    builtins = builtin_names()
    for name, tool in _PLUGIN_TOOLS.items():
        if name in builtins or (tool.mutating and not allow_write):
            continue
        out[name] = tool
    return out


# --- Lab allowlist (rodeo lab add) -----------------------------------------

def _is_url(value: str) -> bool:
    return "://" in value or value.startswith(("git@", "github:", "gitlab:"))


def check_paths(tool: Tool, params: dict, *, lab_dir: Path | None) -> str | None:
    """None when ``params`` may run, else why not. No lab list: always None."""
    from ..config import ConfigError
    from ..lab_allowlist import allowlist_path, is_listed, load_labs, resolve_arg

    try:
        labs = load_labs()
    except ConfigError as exc:
        return f"refused: {exc}"
    if labs is None:
        return None
    where = f"(see rodeo lab list; {allowlist_path()})"
    base = params.get("lab_dir") or lab_dir or os.getcwd()
    if tool.lab_scoped:
        real = resolve_arg(str(base), None)
        if not is_listed(real, labs):
            return f"refused: lab directory {real} is not in the lab list {where}"
        # rodeo falls back to a parent lab or ~/.rodeo/last_lab when the working
        # directory holds no plan: that lab may be outside the list.
        seeds_here = tool.name == "rodeo_init" or (tool.name == "rodeo_up" and params.get("dir"))
        if not seeds_here and not any(os.path.exists(os.path.join(real, m)) for m in _LAB_MARKERS):
            return (f"refused: {real} holds no rodeo-plan.yaml or definition.yaml; with a lab "
                    "list, tools run only in a listed lab itself"
                    + (" (or pass dir)" if tool.name == "rodeo_up" else ""))
    for arg in tool.args:
        if not arg.path or params.get(arg.name) in (None, ""):
            continue
        value = params[arg.name]
        if not isinstance(value, str):
            return f"refused: {arg.name} must be a string"
        if arg.path == "profile":
            from ..labseed import PROFILE_EXAMPLE, canonical_profile

            if _is_url(value) or canonical_profile(value, warn=False) not in PROFILE_EXAMPLE:
                return (f"refused: {arg.name} {value!r}: with a lab list only bundled profiles "
                        f"are accepted ({', '.join(sorted(PROFILE_EXAMPLE))})")
            continue
        real = resolve_arg(value, base)
        if not is_listed(real, labs):
            return f"refused: {arg.name} {real} is not inside a listed lab {where}"
    return None


def warn_if_no_allowlist(stream=None) -> None:
    """One stderr line when MCP tools accept any directory (no lab list yet)."""
    from ..config import ConfigError
    from ..lab_allowlist import allowlist_path, load_labs

    try:
        if load_labs() is not None:
            return
    except ConfigError:
        return  # check_paths refuses every call and says why
    print(f"rodeo mcp: no {allowlist_path()}: tools accept any directory. "
          "Run 'rodeo lab add <lab>' to limit them to listed labs.",
          file=stream or sys.stderr)


def call(
    tool: Tool,
    params: dict,
    *,
    lab_dir: Path | None,
    cancel: threading.Event | None = None,
) -> tuple[bool, str]:
    """Run ``tool`` with ``params``. Returns (ok, text); never raises for a failed run.

    Setting ``cancel`` stops a running rodeo command (its whole process group).
    """
    refused = check_paths(tool, params, lab_dir=lab_dir)
    if refused:
        return False, refused
    cwd = params.get("lab_dir") or lab_dir
    if tool.mutating:
        from ..hooks import begin_operation

        begin_operation(str(cwd or os.getcwd()), f"mcp.{tool.name}")
    if tool.handler is not None:
        return True, tool.handler(params)
    kwargs: dict = {"cwd": cwd}
    if cancel is not None:
        kwargs["cancel"] = cancel
    if tool.timeout is not None:
        kwargs["timeout"] = tool.timeout
    code, output = run_rodeo(tool.argv(params), **kwargs)
    text = output.strip() or "(no output)"
    if code != 0:
        return False, f"rodeo exited {code}\n\n{text}"
    return True, text
