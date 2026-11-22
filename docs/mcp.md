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
| `rodeo_plan` | `rodeo plan` (`output: json` for one JSON object, see below) |
| `rodeo_status` | `rodeo status` |

| `rodeo_plan` | `rodeo plan` |
| `rodeo_status` | `rodeo status`; `cloud: true` runs `rodeo status --cloud` (the AWS host's deploy) |
| `rodeo_list` | `rodeo list` |
| `rodeo_logs` | `rodeo logs --no-follow` |
| `rodeo_instances_list` | `rodeo instances list` |
| `rodeo_fleet_status` | `rodeo fleet status` |
| `rodeo_fleet_doctor` | `rodeo fleet doctor` |
| `rodeo_preflight` | `rodeo deploy --check` — validate, deploy nothing |

Lifecycle (only with `--allow-write`):

| Tool | rodeo command |
|---|---|
| `rodeo_up` | `rodeo up --yes --no-tmux`; for target `aws` it adds `--detach` (see below) |
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

A call that times out or that the client cancels (`notifications/cancelled`)
stops its whole process group: SIGTERM, then SIGKILL after 5 seconds. Write
tools that need root re-exec through `sudo`, so their `rodeo` and its
`ansible-playbook`/`ssh` children run as root, out of the server's reach. sudo
relays the SIGTERM to that root `rodeo`, which stops the processes it started
(its descendants, plus its process group when it leads one) with SIGKILL after
3 seconds for anything that ignores SIGTERM, and exits. It never signals the
rest of a group it only inherited, such as the script or `timeout` that ran
`sudo rodeo`. Local deploys
from MCP need NOPASSWD sudo, since there is no terminal to type a password in.

## Plan as JSON

`rodeo plan --output json` (MCP: `rodeo_plan` with `output: json`) prints one
object: `schema` (1), `name`, `type`, `host_reachable`, `creates`, `changes`
(`kind` is memory, vcpu or dhcp, with `from` and `to`), `unchanged`,
`foreign_vms`, `network`, `storage` (`image_dir`, `downloads`), `phases`
(`state` is done, drift, guarded, pending or failed), `phases_pending`,
`summary` and `config_error`. A plan that fails to load prints only
`{"schema": 1, "error": "..."}`.

The object holds no credential values: `plan` resolves no `??` placeholder,
never reads `secrets.yaml` or the lab secrets file, and masks the plan's own
literal credential values and any `user:password@` in a URL. The exit code
follows the text output: 2 when deploy would refuse the plan (an untrusted lab,
an invalid value), even though the JSON was printed. A `??` source in a trusted
lab is deferred to deploy and is not a failure.

## Plugin tools

A plugin (a `rodeo.plugins` entry point) adds tools with
`rodeo.mcp.tools.register_mcp_tool(tool, mutating=..., lab_scoped=...)`. Both
flags are required. A built-in name, or a name another plugin took, is refused:
plugins can't replace a built-in tool. A mutating plugin tool is offered only
with `--allow-write`, and every plugin tool goes through the lab list above
(`lab_dir` when lab-scoped, plus each `Arg` declared with `path="path"`).

### `rodeo_up` on AWS returns a pid

An AWS deploy takes one to two hours. When the target is `aws` (the `target`
parameter, else the plan's `deployment_target`), `rodeo_up` passes `--detach`:
rodeo provisions the EC2 host, starts `rodeo up` there and returns one JSON
object, `{"state": "started", "pid", "start", "log", "host"}`. The deploy keeps
running on the host. Follow it with `rodeo_status` and `cloud: true`, which
reports `running`, `exited` with the exit code, `gone` (killed or the host
rebooted) or `none`, plus the tail of the log. A deploy that is already running
there comes back as a tool error carrying
`{"state": "already_running", "pid", "log", "host"}` (rodeo exit 75).

Local deploys have no detach yet: `rodeo_up` for any other target blocks until
the deploy ends, within the server's timeout.

## Adding a tool

Add a row to `READ_TOOLS` or `WRITE_TOOLS` in `rodeo/mcp/tools.py`: the rodeo
subcommand, its typed `Arg`s, and any fixed flags. Schema, dispatch and the
write gate follow from that row — `rodeo/mcp/server.py` needs no change.
