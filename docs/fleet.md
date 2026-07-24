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
| **F2.1** | Failure forensics | `rodeo fleet diagnose` |
| **F3** | MCP (not yet) | — |
| **F4** | Host-acquire (planned) | `fleet provision` / `deprovision` — AWS first, then GCP, then Hetzner Cloud |

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
- Not in scope yet: MCP (F3); host-acquire F4 beyond the AWS→GCP→Hetzner sequence;
  Equinix (out of scope); shared secrets; changing the phase pipeline.

---

## F4 — Host-acquire (planned)

Provision KVM-capable hosts from the laptop, merge them into `workshop.yaml`, then
use the normal fleet converge loop. **Not implemented yet.**

| Order | Provider | Approach |
|-------|----------|----------|
| **F4a** | AWS | Python + `boto3` (`pip install 'rodeo-cli[aws]'`) — primary |
| **F4b** | GCP | Python + `google-cloud-compute` — same `HostProvider` interface |
| **F4c** | Hetzner Cloud | Python + `hcloud` — after GCP; nested KVM must be validated for labs |

Equinix Metal is **out of scope** (service sunset).

```bash
rodeo fleet provision -f workshop.yaml    # create/reuse → write hosts[]
rodeo fleet doctor -f workshop.yaml
rodeo fleet deploy -f workshop.yaml
rodeo fleet deprovision -f workshop.yaml --yes
```

Providers stop at inventory. Deploy/diagnose/retry stay OpenSSH-only.

### Design: `HostProvider` Protocol (no code yet)

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
| `name` | Stable id: `aws` \| `gcp` \| `hetzner` |
| `validate(config) → None` | Fail closed (`ConfigError`) on missing/invalid fields **for that type only** |
| `plan(spec, config) → list[action]` | Optional dry-run: create / reuse / noop per desired host id (nice-to-have for F4a) |
| `provision(spec, config) → list[ProvisionedHost]` | Idempotent: reuse instances tagged for this workshop+host id; create the rest; wait until **running** + **SSH BatchMode** succeeds |
| `deprovision(spec, config) → list[DeprovisionResult]` | Destroy **only** resources with ownership tags below; refuse untagged |

**Shared (not per-provider)**

- SSH wait / probe via existing `rodeo/fleet/ssh_exec.py` (no paramiko).
- Inventory merge: write/update `hosts[]` by `id`; do not delete static hosts unless `--prune`.
- Optional extras: `[aws]`, `[gcp]`, `[hetzner]` so core install stays light.

**Ownership tags** (every created instance; same keys on all clouds)

| Tag / label key | Value |
|-----------------|-------|
| `ManagedBy` | `rodeo-fleet` |
| `rodeo-workshop` | `workshop.yaml` `name:` |
| `rodeo-host-id` | inventory host `id` (e.g. `student-01`) |

GCP uses labels (DNS-1123); normalize keys to lowercase where the cloud requires it, but keep the same logical names.

**Non-goals for the Protocol**

- No multi-cloud “common instance type” enum — size/image stay provider-specific.
- No shared secrets / AMI publishing pipeline in F4.
- No Libcloud / OpenTofu required for the default path.

### Design: `workshop.yaml` `provider:` schema

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
  type: aws                         # aws | gcp | hetzner  (required if provider: present)
  count: 12                         # how many hosts to ensure when hosts: [] or undersized
  # host_id_prefix: student-        # default "student-"; ids student-01 … student-N
  # Optional overrides applied to every provisioned host:
  # labels: { room: a, event: emea }
hosts: []                           # empty → provision creates; or pre-seed static + cloud mix
```

Validation rules (fail closed):

- `provider.type` ∈ `{aws, gcp, hetzner}`.
- `provider.count` integer 1–64 when set; if `hosts:` non-empty and count omitted, ensure exactly those ids (reuse/create by `rodeo-host-id`).
- `defaults.identity_file` (or equivalent key material) required for provision SSH wait.
- Type-specific required keys enforced by that adapter’s `validate()` only.

#### `provider.type: aws` (F4a)

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
  # volume_size_gib: 500            # optional root / data disk
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

#### `provider.type: hetzner` (F4c)

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
**Gate:** do not mark F4c complete until `fleet doctor` shows nested KVM on a real
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
