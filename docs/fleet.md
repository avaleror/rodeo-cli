# Fleet — workshop fan-out

Laptop-side control plane that runs the same single-host `rodeo` checks on many
KVM hosts over **OpenSSH**. Remotes still converge labs independently; fleet only
orchestrates.

## Status (v1 / F1)

| Command | What it does |
|---------|----------------|
| `rodeo fleet doctor -f workshop.yaml` | Remote `rodeo doctor --output json` on each host |
| `rodeo fleet status -f workshop.yaml` | Remote `rodeo status --output json` in `lab.dir` |

**Not in F1:** `fleet deploy` / `retry`, MCP, cloud host provisioning.

Host CLI prerequisites (already on each lab machine after `install.sh`):

```bash
rodeo doctor --output json
rodeo status --output json   # from the lab directory
```

## Inventory (`workshop.yaml`)

```yaml
name: suse-virt-rodeo-emea
lab:
  dir: /root/suse-virt-workshop   # remote cwd for fleet status
defaults:
  ssh_user: root
  # identity_file: /home/you/.ssh/id_ed25519
  # ssh_options: ["ProxyJump=bastion.example"]
hosts:
  - id: student-01
    ssh: 203.0.113.11            # host or user@host
    public_ip: 203.0.113.11      # reserved for a future access sheet
    labels: { room: a }
  - id: student-02
    ssh: root@lab-02.example
    labels: { room: b }
```

Validation is fail-closed: unique `id`, required `ssh`, required `lab.dir`.

## Usage

```bash
# All hosts
rodeo fleet doctor -f workshop.yaml
rodeo fleet status -f workshop.yaml --output json

# Selectors
rodeo fleet doctor -f workshop.yaml --label room=a
rodeo fleet status -f workshop.yaml --host student-01 --host student-02

# Parallelism (default 8)
rodeo fleet doctor -f workshop.yaml -j 4
```

Exit code `0` only when every selected host succeeds. Partial results are still
printed (table or JSON). For `fleet doctor`, "succeeds" means the host is
actually workshop-ready (KVM present, nested virt on, required tools present,
a bundled profile fits available RAM) — not just that SSH connected and
`rodeo doctor` returned. Local `rodeo doctor` is a read-only advisory command
and always exits 0 on its own, so this readiness check is computed fleet-side
from the JSON report (`rodeo/fleet/doctor.py::_readiness_problems`).

## OpenSSH requirements

- Key-based auth with `BatchMode=yes` (no password prompts).
- Host keys are not verified (`StrictHostKeyChecking=no`, `UserKnownHostsFile=/dev/null`) — same trade-off `rodeo/ssh.py` already makes for host→VM connections. Workshop hosts are treated as ephemeral lab machines, not long-lived trusted infrastructure; nothing needs pre-accepting in `known_hosts` before the first run.
- `ssh` on the laptop PATH; Agent / `ProxyJump` / `identity_file` work as usual.
- On each remote: `rodeo` on PATH for the SSH user (same as workshop install).

Fleet does **not** sudo-re-exec on the laptop. If remote `status` fails for
libvirt permissions, the host row shows the remote stderr — fix access on that
host (run as root or libvirt group), then re-run.

## Design note

`rodeo/ssh.py` remains host→VM lab options. Laptop→host SSH lives in
`rodeo/fleet/ssh_exec.py` so the two layers stay separate.
