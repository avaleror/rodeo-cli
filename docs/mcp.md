# MCP server

`rodeo mcp` exposes rodeo to MCP clients — Claude Code, editors, agent
runtimes — over the MCP **stdio** transport (newline-delimited JSON-RPC 2.0).
It has no extra dependency: the protocol is implemented in `rodeo/mcp/`.

Every tool runs the rodeo CLI itself, so the surface covers **native** phase
pipelines (`suse-virt`, `rancher`, `suse-edge`, `suse-telco`) and **external
engines** (`lab-in-a-box`, plugin-provided types) through the same commands an
operator types.

## Run it

```bash
rodeo mcp --list-tools                      # what would be offered
rodeo mcp                                   # read-only server
rodeo mcp --allow-write --lab-dir ~/rodeo-labs/harvester
```

| Option | What it does |
|---|---|
| `--allow-write` | Also offer the lifecycle tools. Off by default, so the server cannot deploy, clean or destroy anything. |
| `--lab-dir DIR` | Default lab directory for lab-scoped tools. Without it, the detected lab dir is used, else the working directory. |
| `--list-tools` | Print the offered tools and exit. |

Lab-scoped tools also take a per-call `lab_dir` argument, which overrides the
server default — rodeo commands are lab-directory relative.

## Limit the server to listed labs

```bash
rodeo lab add ~/rodeo-labs/harvester    # needs a terminal
rodeo lab list
rodeo lab remove ~/rodeo-labs/harvester # needs a terminal
```

`rodeo lab add` writes the lab's realpath to `~/.rodeo/labs.yaml` (mode 0600).
Once that file exists, even with an empty list, every path an MCP tool receives
must resolve to a listed lab or a path inside one: `lab_dir` (or the server
default), `rodeo_up` `dir`, `rodeo_init` `target_dir`, `rodeo_instances_list`
`dir`, `rodeo_new` `from_zip` and `from_lab_json`, and fleet `file`. Symlinks
and `..` are resolved first, and a relative path counts from the tool's lab
directory. `rodeo_new` `from_base` then takes bundled profile names only, no
URL. A lab-scoped tool also needs `rodeo-plan.yaml` or `definition.yaml` in
that directory itself, because rodeo would otherwise fall back to a parent lab
or the last lab used (`rodeo_init`, and `rodeo_up` with `dir`, may seed an
empty one). A refused call is a tool error and runs nothing.

Without the file the server keeps today's behaviour, accepts any directory and
prints one warning on stderr when it starts.

The list is ordinary user data: it stops an MCP client or a confused model from
pointing tools at other directories, not a process running as the same user,
which can edit the file.

## Register it with a client

Claude Code (`~/.claude.json` or project `.mcp.json`):

```json
{
  "mcpServers": {
    "rodeo": {
      "command": "rodeo",
      "args": ["mcp", "--allow-write", "--lab-dir", "/root/rodeo-labs/harvester"]
    }
  }
}
```

Drop `--allow-write` for an agent that should only observe a lab.

## Tools

Read-only (always offered):

| Tool | rodeo command |
|---|---|
| `rodeo_version` | `rodeo --version` |
| `rodeo_help` | `rodeo [COMMAND] --help` — discover commands with no tool of their own |
| `rodeo_engines` | registered lab types, each tagged `native` or `external` (JSON) |
| `rodeo_doctor` | `rodeo doctor` |
| `rodeo_profiles` | `rodeo profiles` |
| `rodeo_plan` | `rodeo plan` |
| `rodeo_status` | `rodeo status` |
| `rodeo_list` | `rodeo list` |
| `rodeo_logs` | `rodeo logs --no-follow` |
| `rodeo_instances_list` | `rodeo instances list` |
| `rodeo_fleet_status` | `rodeo fleet status` |
| `rodeo_fleet_doctor` | `rodeo fleet doctor` |
| `rodeo_preflight` | `rodeo deploy --check` — validate, deploy nothing |

Lifecycle (only with `--allow-write`):

| Tool | rodeo command |
|---|---|
| `rodeo_up` | `rodeo up --yes --no-tmux` |
| `rodeo_deploy` | `rodeo deploy --no-tui` (`from_phase` resumes a phase) |
| `rodeo_init` | `rodeo init` |
| `rodeo_new` | `rodeo new` |
| `rodeo_start`, `rodeo_stop`, `rodeo_start_if_needed`, `rodeo_restart` | VM power |
| `rodeo_clean` | `rodeo clean --yes` — destructive |
| `rodeo_destroy` | `rodeo destroy --yes` — destructive |
| `rodeo_set_password` | `rodeo set-password` |
| `rodeo_install_extensions` | `rodeo install-extensions` |
| `rodeo_self_update` | `rodeo self-update` |

Every tool is non-interactive: no TUI, no tmux wrap, no prompts, no colour. A
failing command comes back as a tool error carrying rodeo's exit code and the
tail of its output, never as a protocol error.

## Plugin tools

A plugin (a `rodeo.plugins` entry point) adds tools with
`rodeo.mcp.tools.register_mcp_tool(tool, mutating=..., lab_scoped=...)`. Both
flags are required. A built-in name, or a name another plugin took, is refused:
plugins can't replace a built-in tool. A mutating plugin tool is offered only
with `--allow-write`, and every plugin tool goes through the lab list above
(`lab_dir` when lab-scoped, plus each `Arg` declared with `path="path"`).

## Adding a tool

Add a row to `READ_TOOLS` or `WRITE_TOOLS` in `rodeo/mcp/tools.py`: the rodeo
subcommand, its typed `Arg`s, and any fixed flags. Schema, dispatch and the
write gate follow from that row — `rodeo/mcp/server.py` needs no change.
