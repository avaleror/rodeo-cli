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

## Keep the agent on the MCP tools: `rodeo agent-setup`

An agent with a shell can skip the MCP server and type `rodeo up` or `sudo
virsh destroy` itself. `rodeo agent-setup` writes a Claude Code kit into the
lab that catches this mistake:

```bash
cd ~/rodeo-labs/harvester
rodeo agent-setup            # writes .claude/settings.json
rodeo agent-setup --check    # exit 1 if the kit is missing
```

It merges into `<lab>/.claude/settings.json`:

- `permissions.deny`: `Bash(rodeo)`, `Bash(rodeo *)`, `Bash(sudo *)`,
  `Bash(virsh *)`, and `Bash(ssh *HOST*)` / `Bash(scp *HOST*)` for every VM
  name, VM address, VIP and AWS host the plan names.
- A `PreToolUse` hook on Bash (`python -I .../rodeo/agent_guard.py`) that also
  catches the chained forms (`cd lab && rodeo up`, `bash -c '...'`,
  `$(rodeo status)`) and tells the model to use the MCP tools instead.

Your own rules and hooks are kept, odd or invalid JSON is refused rather than
overwritten, a symlinked settings file or `.claude` directory is refused, and
a second run changes nothing. Re-run it after the lab's hosts change (a new
AWS host, for example): new host rules are added and rodeo's hook entry is
updated in place. `.claude/` is not part of the lab hash, so writing the kit
does not untrust a lab.

**This is a mistake filter, not a security boundary.** It is a settings file
the same user can edit or delete, so it stops a confused model, not a hostile
one.

MCP local deploys (`rodeo_up`, `rodeo_deploy` and the other write tools that
need root) still need NOPASSWD sudo for the user running the MCP server. The
kit only denies `sudo` in the agent's shell, not in the server.

`rodeo doctor` shows whether the kit is installed in the current lab (and
`rodeo doctor --output json` carries it as `agent_kit`). It can tell that the
kit is installed, not whether an agent session is running without it (when
doctor itself runs from a Claude Code shell without the kit, it says so). Only
Claude Code has a kit today. When doctor finds a Gemini CLI or Codex CLI
config, it reports "no kit for this client".

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

## Adding a tool

Add a row to `READ_TOOLS` or `WRITE_TOOLS` in `rodeo/mcp/tools.py`: the rodeo
subcommand, its typed `Arg`s, and any fixed flags. Schema, dispatch and the
write gate follow from that row — `rodeo/mcp/server.py` needs no change.
