# Fleet — multi-host workshop orchestration

Laptop-side control plane that drives **many remote KVM hosts** over OpenSSH.
Each host still runs normal single-host `rodeo`; fleet only fans out and tracks
jobs. Engine phases, Ansible roles, and nested networking are unchanged.

See also: [Get started](get-started.md) (single host), [Architecture](architecture.md),
[ROADMAP.md](https://github.com/avaleror/rodeo-cli/blob/main/ROADMAP.md) (Phase I).

## Capabilities by phase

| Phase | Status | What | Commands |
|-------|--------|------|----------|
| **F0** | Shipped | Machine-readable host CLI | `rodeo doctor --output json`, `rodeo status --output json` |
| **F1** | Shipped | Inventory + read-only fan-out | `rodeo fleet doctor`, `rodeo fleet status` |
| **F2** | Shipped | Deploy / retry / access sheet | `rodeo fleet deploy`, `retry`, `access` |
| **F2.1** | Shipped | Failure forensics | `rodeo fleet diagnose` |
| **F3** | Roadmap | MCP tools on top of fleet | — |
| **F4a** | Shipped (MVP) | AWS host-acquire | `rodeo fleet provision`, `deprovision` |
| **F4b–d** | Roadmap | GCP → Vultr BM → Hetzner | — |

Host prerequisites (after [`install.sh`](https://github.com/avaleror/rodeo-cli/blob/main/install.sh) on each lab machine):

```bash
rodeo doctor --output json
# from the lab directory:
rodeo status --output json
```

---

## Roadmap

What is **not** shipped yet for Fleet. Full checklist: [ROADMAP Phase I](https://github.com/avaleror/rodeo-cli/blob/main/ROADMAP.md#phase-i--fleet--workshop-fan-out).

### F3 — MCP (planned)

Thin MCP tools that wrap existing fleet commands (`doctor`, `status`, `deploy`,
`diagnose`, `retry`, `access`) so an IDE or agent can drive a workshop without
reimplementing OpenSSH fan-out. No new provision logic inside MCP — that stays
CLI-first under F4.

### F4 — Host-acquire

**Create KVM hosts from the laptop**, merge into `workshop.yaml`, then run the
normal converge loop. Providers stop at inventory; deploy / diagnose / retry stay
OpenSSH-only.

| Order | Provider | Status |
|-------|----------|--------|
| **F4a** | **AWS** (`boto3`, `pip install 'rodeo-cli[aws]'`) | **MVP shipped** — create/reuse by tags, wait running + SSH, write `hosts[]`, terminate tagged only |
| **F4b** | **GCP** (`google-cloud-compute`) | Planned |
| **F4c** | **Vultr Bare Metal** (`[vultr]` extra) | Planned — after GCP; real metal for nested KVM |
| **F4d** | **Hetzner Cloud** (`hcloud`) | Planned — after Vultr; nested KVM must be validated |

**Out of scope:** Equinix Metal (service sunset). Shared secrets across hosts.
Changing the nested phase engine for multi-host.

**MVP gaps (AWS):** no `plan` dry-run yet; `deprovision` does not rewrite `hosts[]`
(terminate only — edit or re-provision to refresh YAML); AMI/name filters and
auto SG later.

```bash
pip install 'rodeo-cli[aws]'   # once, on the laptop
# AWS creds via standard boto3 chain (env / profile / instance role) — not in YAML
rodeo fleet provision -f workshop.yaml    # create/reuse → write hosts[]
rodeo fleet doctor -f workshop.yaml
rodeo fleet deploy -f workshop.yaml
rodeo fleet deprovision -f workshop.yaml --yes
```

Shared `HostProvider` Protocol and `provider:` YAML schema:
[Host-acquire design](#f4-host-acquire-design) below.

### Related (not Fleet-only)

Single-host `deployment_target: aws` (Phase E MVP) shares the same
`rodeo/providers/aws` adapter and `provider:` YAML schema as Fleet F4a.
On the EC2 guest the lab still runs as `baremetal`.

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
  target: baremetal                 # baremetal | instruqt (use baremetal for AWS hosts)
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
rodeo fleet diagnose -f workshop.yaml        # pull logs for failed hosts
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

Hosts whose **cacheable** phases are already all `completed` are **skipped**
unless `--force`. The `apply` phase is never cached (re-run every local deploy)
and does not block this check.

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

### Diagnose (failure forensics)

`fleet status` / the job file tell you *which* host and phase failed. To see
*why*, pull logs onto the laptop:

```bash
rodeo fleet diagnose -f workshop.yaml                  # failed hosts (default)
rodeo fleet diagnose -f workshop.yaml --all-selected   # every selected host
rodeo fleet diagnose -f workshop.yaml -o /tmp/diag --output json
```

Per host, under `<inventory>.diagnose-<utc>/<host-id>/` (or `-o`):

| Artifact | Source |
|----------|--------|
| `status.json` | remote `rodeo status --output json` |
| `logs/*.log` | tails of `~/.rodeo/logs/` (incl. `fleet-up.log`) |
| `meta/state/` | `~/.rodeo/state/*.yaml` phase cache |
| `meta/tmux-pane.txt` | tmux pane capture when the job session still exists |
| `summary.json` | short failure digest for that host |
| `index.json` | workshop-wide index (also printed with `--output json`) |

Does not print or collect secrets. Exit `0` when collection succeeds; `1` if SSH
or archive extract fails for any host.

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
- See [Roadmap](#roadmap) for F3 MCP and F4 host-acquire. Equinix is out of scope.
  Shared secrets and changing the phase pipeline stay out of Fleet.

---

## F4 host-acquire design

Shared Protocol / schema for host-acquire. **AWS (F4a) MVP is implemented** in
`rodeo/providers/` for both Fleet and single-host `deployment_target: aws`.
GCP / Vultr / Hetzner remain stubs. Summary in [Roadmap](#roadmap).

**Important:** AWS-provisioned workshop hosts run the lab as
`lab.target: baremetal` (full firewalld / DNAT / `finalise`). `deployment_target: aws`
is the laptop control-plane mode (`rodeo up --target aws`); do not set
`lab.target: aws` in `workshop.yaml`.

### `HostProvider` Protocol

Shared contract in planned `rodeo/providers/`. Fleet CLI never imports boto3/GCP/hcloud
directly — only the registry + this surface.

**Types (conceptual)**

| Name | Role |
|------|------|
| `ProviderConfig` | Parsed `workshop.yaml` → `provider:` mapping (type + type-specific fields) |
| `ProvisionSpec` | Workshop name, desired count / host ids, SSH defaults, labels to apply |
| `ProvisionedHost` | Maps 1:1 onto inventory `hosts[]`: `id`, `ssh`, `public_ip`, `labels`, optional `provider_id` (cloud instance id) |
| `DeprovisionResult` | Per-host outcome: destroyed / skipped / error |

**Required operations**

| Method | Behavior |
|--------|----------|
| `name` | Stable id: `aws` \| `gcp` \| `vultr` \| `hetzner` |
| `validate(config) → None` | Fail closed (`ConfigError`) on missing/invalid fields **for that type only** |
| `plan(spec, config) → list[action]` | Optional dry-run: create / reuse / noop per desired host id (nice-to-have for F4a) |
| `provision(spec, config) → list[ProvisionedHost]` | Idempotent: reuse instances tagged for this workshop+host id; create the rest; wait until **running** + **SSH BatchMode** succeeds |
| `deprovision(spec, config) → list[DeprovisionResult]` | Destroy **only** resources with ownership tags below; refuse untagged |

**Shared (not per-provider)**

- SSH wait / probe via existing `rodeo/fleet/ssh_exec.py` (no paramiko).
- Inventory merge: write/update `hosts[]` by `id`; do not delete static hosts unless `--prune`.
- Optional extras: `[aws]`, `[gcp]`, `[vultr]`, `[hetzner]` so core install stays light.

**Ownership tags** (every created instance; same keys on all clouds)

| Tag / label key | Value |
|-----------------|-------|
| `ManagedBy` | `rodeo` |
| `rodeo-workshop` | plan `name:` / workshop `name:` |
| `rodeo-host-id` | inventory host `id` (e.g. `student-01`) or `primary` for single-host |

GCP uses labels (DNS-1123); normalize keys to lowercase where the cloud requires it, but keep the same logical names.

**Non-goals for the Protocol**

- No multi-cloud “common instance type” enum — size/image stay provider-specific.
- No shared secrets / AMI publishing pipeline in F4.
- No Libcloud / OpenTofu required for the default path.

### `workshop.yaml` `provider:` schema

Top-level `provider:` is optional. Absent → today’s behavior (static `hosts:` only).
Present → `fleet provision` / `deprovision` are valid; `type` selects the adapter.

#### Common fields

```yaml
name: suse-virt-rodeo-emea          # used as rodeo-workshop tag
lab:
  dir: /root/suse-virt-workshop
  source: git:https://github.com/example/suse-virt-workshop.git
  # … existing lab keys unchanged …
defaults:
  ssh_user: root                     # or ec2-user / sles / … per AMI
  identity_file: ~/.ssh/rodeo-workshop.pem
  # ssh_options: ["ProxyJump=bastion"]
provider:
  type: aws                         # aws | gcp | vultr | hetzner  (required if provider: present)
  count: 12                         # how many hosts to ensure when hosts: [] or undersized
  # host_id_prefix: student-        # default "student-"; ids student-01 … student-N
  # Optional overrides applied to every provisioned host:
  # labels: { room: a, event: emea }
hosts: []                           # empty → provision creates; or pre-seed static + cloud mix
```

Validation rules (fail closed):

- `provider.type` ∈ `{aws, gcp, vultr, hetzner}`.
- `provider.count` integer 1–64 when set; if `hosts:` non-empty and count omitted, ensure exactly those ids (reuse/create by `rodeo-host-id`).
- `defaults.identity_file` (or equivalent key material) required for provision SSH wait.
- Type-specific required keys enforced by that adapter’s `validate()` only.

#### `provider.type: aws` (F4a)

Prefer **metal** or large Nitro nested-virt types (≥128 GiB RAM for full Harvester /
Edge). Set `volume_size_gib: 500` (or larger) so nested Harvester disks fit.
Tiny / burstable types are rejected at validate.

```yaml
provider:
  type: aws
  count: 12
  region: eu-central-1              # required
  instance_type: m7i.metal-24xl     # required; nested-virt capable / metal
  ami: ami-0123456789abcdef0        # required (or ami_name_filter later)
  key_name: rodeo-workshop          # required EC2 key pair name
  subnet_id: subnet-0abc…           # required
  security_group_ids:               # required; must allow 22, 8443, 30002 as needed
    - sg-0abc…
  # associate_public_ip: true       # default true
  # nested_virtualization: true     # if using Nitro nested-virt types instead of metal
  # volume_size_gib: 500            # recommended ≥500 for Harvester
```

#### `provider.type: gcp` (F4b)

```yaml
provider:
  type: gcp
  count: 12
  project: my-gcp-project           # required
  zone: europe-west3-a              # required
  machine_type: n2-standard-32      # required
  image: projects/…/global/images/… # required (or family)
  network: default                  # or full URL
  subnetwork: regions/…/subnetworks/…
  # enable_nested_virtualization: true   # default true for rodeo
  # min_cpu_platform: "Intel Cascade Lake"
  # tags: [rodeo-fleet]             # GCP network tags for firewall
```

Auth: Application Default Credentials / service account — not stored in `workshop.yaml`.

#### `provider.type: vultr` (F4c)

```yaml
provider:
  type: vultr
  count: 12
  region: ewr                       # required (Vultr location id)
  plan: vbm-8c-128gb                # required — use ≥128 GiB for full Harvester labs
  os_id: 2284                       # required (or snapshot_id / iPXE)
  # sshkey_id: ["…"]                # Vultr SSH key ids
  # firewall_group_id: "…"          # must allow 22 / UI ports for access sheet
  # label_prefix: rodeo-            # optional
```

Auth: `VULTR_API_KEY` (API key may require IP allowlisting). Prefer REST `/v2/bare-metals`
or the OpenAPI client over a thin community wrapper. **Bare metal only** for nested KVM —
not Vultr Cloud VPS. Gate plans by RAM for the chosen profile (`rodeo doctor`).

#### `provider.type: hetzner` (F4d)

```yaml
provider:
  type: hetzner
  count: 12
  location: fsn1                    # required
  server_type: cpx51                # required — must pass nested-KVM validation for labs
  image: rocky-9                    # required (or snapshot id); SLES path TBD
  # ssh_keys: ["rodeo-workshop"]    # Hetzner SSH key names/ids
  # networks: []                    # optional private networks
  # firewalls: []                   # must expose 22 / UI ports for access sheet
```

Auth: `HCLOUD_TOKEN` (or future `??` secret key) — not in plaintext in the inventory.
**Gate:** do not mark F4d complete until `fleet doctor` shows nested KVM on a real
Hetzner Cloud type used for workshops.

#### Merge semantics

| Situation | `fleet provision` |
|-----------|-------------------|
| `hosts: []`, `count: N` | Create/reuse `student-01`…`student-N`; write `hosts[]` |
| `hosts:` lists ids | Ensure those ids only (ignore count or require count ≥ len) |
| Instance already tagged `rodeo-workshop` + `rodeo-host-id` | Reuse; refresh `ssh` / `public_ip` in YAML |
| `fleet deprovision` | Terminate/delete tagged instances; clear or mark cloud-sourced hosts in YAML |

Static hosts (no `labels.provider` / no cloud `provider_id`) are never destroyed by
deprovision unless explicitly selected later (`--all-tagged` stays the default safety).
