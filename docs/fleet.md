# Fleet — multi-host workshop orchestration

Laptop-side control plane that drives **many remote KVM hosts** over OpenSSH.
Each host still runs normal single-host `rodeo`; fleet only fans out and tracks
jobs. Engine phases, Ansible roles, and nested networking are unchanged.

See also: [Get started](get-started.md) (single host), [Architecture](architecture.md).

## Capabilities by phase

| Phase | What shipped | Commands |
|-------|----------------|----------|
| **F0** | Machine-readable host CLI | `rodeo doctor --output json`, `rodeo status --output json` |
| **F1** | Inventory + read-only fan-out | `rodeo fleet doctor`, `rodeo fleet status` |
| **F2** | Deploy / retry / access sheet | `rodeo fleet deploy`, `retry`, `access` |
| **F3** | MCP (not yet) | — |

Host prerequisites (after [`install.sh`](https://github.com/avaleror/rodeo-cli/blob/main/install.sh) on each lab machine):

```bash
rodeo doctor --output json
# from the lab directory:
rodeo status --output json
```

---

## F0 — JSON on a single host

Structured reports live in `rodeo/service/` so CLI and fleet share one shape.

```bash
rodeo doctor --output json
rodeo status --output json   # requires a lab (cwd or --config-dir)
```

Default `--output text` keeps the existing Rich tables.

---

## Inventory (`workshop.yaml`)

```yaml
name: suse-virt-rodeo-emea
lab:
  dir: /root/suse-virt-workshop     # remote path (status + deploy cwd)
  # F2 — one of:
  source: git:https://github.com/avaleror/suse-virt-workshop.git
  # profile: harvester              # alternative: seed bundled/custom profile
  branch: main                      # optional (git only)
  target: baremetal                 # baremetal | instruqt
  concurrency: 4                    # default -j for deploy/retry
  ports:
    harvester: 8443                 # DNAT on host public IP
    rancher: 30002
  # components: [harvester]         # optional — see "Access sheet" below.
  #                                  # Omit to show every URL fleet knows how to build.
defaults:
  ssh_user: root
  # identity_file: /home/you/.ssh/id_ed25519
  # ssh_options: ["ProxyJump=bastion.example"]
hosts:
  - id: student-01
    ssh: 203.0.113.11               # host or user@host
    public_ip: 203.0.113.11         # used by fleet access
    labels: { room: a }
  - id: student-02
    ssh: root@lab-02.example
    public_ip: 203.0.113.12
    labels: { room: b }
```

Validation is fail-closed: unique `id`, required `ssh`, required `lab.dir`.
`lab.source` or `lab.profile` is required only for **deploy** / **retry**.

---

## F1 — doctor and status

```bash
rodeo fleet doctor -f workshop.yaml
rodeo fleet status -f workshop.yaml --output json

rodeo fleet doctor -f workshop.yaml --label room=a
rodeo fleet status -f workshop.yaml --host student-01 -j 4
```

- Exit `0` only when every selected host succeeds.
- **doctor:** remote process exit is not enough — fleet also checks KVM, nested
  virt, core tools, and that a bundled profile fits RAM
  (`rodeo/fleet/doctor.py::_readiness_problems`).
- **status:** runs in `lab.dir` on each host. If `workshop.job.yaml` exists,
  status refreshes per-host job states for later retry.

---

## F2 — deploy, retry, access

### Instructor flow

```bash
rodeo fleet doctor -f workshop.yaml -j 8
rodeo fleet deploy -f workshop.yaml -j 4
rodeo fleet status -f workshop.yaml          # poll until phases complete
rodeo fleet retry -f workshop.yaml --failed-only
rodeo fleet access -f workshop.yaml --output json
```

### What `fleet deploy` does on each host

1. Ensure `rodeo` is on PATH (runs `install.sh` if missing).
2. Sync lab: `git clone` / `git pull --ff-only`, or `rodeo up --no-deploy` for a profile.
3. Start **detached tmux** running `rodeo up --yes --no-tmux` in `lab.dir`
   (session name `rodeo-fleet-<workshop>-<host-id>`).
4. Return immediately — does **not** wait for the 90–150 minute install.
5. Write **`workshop.job.yaml`** beside the inventory (chmod 600).

Hosts whose phases are already all `completed` are **skipped** unless `--force`.

Secrets: generated **per host** by remote `rodeo up` — fleet never scp’s a shared
`secrets.yaml`.

### Job file

```yaml
workshop: suse-virt-rodeo-emea
hosts:
  student-01:
    state: running    # pending | running | ok | failed
    tmux: rodeo-fleet-suse-virt-rodeo-emea-student-01
  student-02:
    state: failed
    last_error: "..."
```

### Retry

```bash
rodeo fleet retry -f workshop.yaml --failed-only   # default
rodeo fleet retry -f workshop.yaml --all-selected  # ignore job failures; use --host/--label
```

Refreshes job state from live `status`, then re-starts deploy with `--force` on
the chosen hosts.

### Access sheet

```bash
rodeo fleet access -f workshop.yaml
```

| id | Harvester | Rancher |
|----|-----------|---------|
| student-01 | `https://203.0.113.11:8443` | `https://203.0.113.11:30002` |

Nested VIP stays `192.168.122.10` inside each host; students use **host public IP +
DNAT**. Passwords are **never** printed — they live on each host in
`~/.rodeo/secrets.yaml`.

By default `access` prints both URLs for every host — fleet has no reliable
local signal for which UIs a given lab actually exposes (a bundled profile
name doesn't map 1:1 to components: e.g. the `test` profile's example dir has
no Rancher node at all). Set `lab.components: [harvester]` or
`[rancher]` in the inventory to suppress the URL(s) that don't apply to your
workshop.

---

## OpenSSH requirements

- Key-based auth with `BatchMode=yes` (no password prompts).
- Host keys are not verified (`StrictHostKeyChecking=no`,
  `UserKnownHostsFile=/dev/null`) — same trade-off as host→VM `rodeo/ssh.py`.
  Workshop hosts are treated as ephemeral lab machines.
- `ssh` on the laptop PATH; Agent / `ProxyJump` / `identity_file` work as usual.
- On each remote: `rodeo` + `tmux` on PATH for the SSH user; typically `root@`.

Fleet does **not** sudo-re-exec on the laptop.

## Design notes

- `rodeo/ssh.py` = host→VM lab connections.
- `rodeo/fleet/ssh_exec.py` = laptop→KVM host.
- Concurrency defaults: doctor/status `-j 8`; deploy/retry use `lab.concurrency`
  (default 4) unless `-j` is set. Prefer low concurrency for deploy (ISO/network).
- Not in scope yet: MCP (F3), Equinix/AWS host provisioning, shared secrets,
  changing the phase pipeline.
