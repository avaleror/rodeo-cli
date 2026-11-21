"""Declarative MCP tool surface over the rodeo CLI.

One entry per tool: the rodeo subcommand it runs, its typed parameters, and
whether it changes state (``mutating``). Mutating tools are only offered when
the server runs with writes enabled. Adding a tool means adding a row here —
no protocol or dispatch code changes.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
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
    """

    name: str
    kind: str
    description: str
    spec: str = ""
    type: str = "string"
    enum: tuple[str, ...] = ()
    required: bool = False

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
         (Arg("dir", "option", "Where the labs are.", "--dir"),)),
    Tool("rodeo_fleet_status",
         "Run status on every host of a fleet inventory.",
         ("fleet", "status"),
         (Arg("file", "option", "Fleet inventory file.", "--file", required=True),
          _OUTPUT,
          Arg("label", "repeat", "Only hosts with these labels.", "--label"),
          Arg("host", "repeat", "Only these host ids.", "--host")),
         timeout=900),
    Tool("rodeo_fleet_doctor",
         "Reachability and readiness of every host of a fleet inventory.",
         ("fleet", "doctor"),
         (Arg("file", "option", "Fleet inventory file.", "--file", required=True), _OUTPUT),
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
          Arg("dir", "option", "Lab directory to create or use.", "--dir"),
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
          Arg("target_dir", "positional", "Directory to seed. Default: the lab dir."),
          Arg("force", "flag", "Overwrite existing files.", "--force")),
         mutating=True, lab_scoped=True),
    Tool("rodeo_new",
         "Create a custom profile from a bundled one, a zip or a lab JSON.",
         ("new",),
         (Arg("name", "positional", "New profile name.", required=True),
          Arg("from_base", "option", "Bundled profile to start from.", "--from"),
          Arg("from_zip", "option", "Profile zip to import.", "--from-zip"),
          Arg("from_lab_json", "option", "lab-in-a-box lab JSON to import.", "--from-lab-json"),
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


def tool_map(*, allow_write: bool) -> dict[str, Tool]:
    """Offered tools by name: read-only always, lifecycle only with writes enabled."""
    tools = list(READ_TOOLS) + (list(WRITE_TOOLS) if allow_write else [])
    return {t.name: t for t in tools}


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
    if tool.handler is not None:
        return True, tool.handler(params)
    cwd = params.get("lab_dir") or lab_dir
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
