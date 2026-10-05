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
| **F4a** | Shipped (MVP) | AWS host-acquire | `rodeo fleet provision`, `deprovision` |
| **F4b–d** | Roadmap | GCP → Vultr BM → Hetzner | — |
| **F5** | Preview (AWS) | Student claim portal: each student opens a link and gets their own lab | `rodeo fleet open-access`, `rodeo fleet portal ...` ([design](claim-portal.md), [plan](claim-portal-plan.md)) |

Host prerequisites (after [`install.sh`](https://github.com/avaleror/rodeo-cli/blob/main/install.sh) on each lab machine):

```bash
rodeo doctor --output json
# from the lab directory:
rodeo status --output json
```

---

## Labs with operator secrets or other UIs

Some labs need values only you have (a lab's `operator_secrets`, e.g. an SCC
regcode or a pre-built image URL). List them in `workshop.yaml`: `fleet deploy`
copies just those keys from your `~/.rodeo/secrets.yaml` to each host (over SSH
stdin, 0600) before `rodeo up`. It stops without touching any host if one is
missing. `ui_ports` adds columns to `fleet access`:

```yaml
lab:
  profile: smlm-workshop
  dir: /root/rodeo-lab
  components: []                 # no Harvester/Rancher UI in this lab
  ui_ports: {smlm: 443}
  operator_secrets: [smlm_image_url, smlm_image_sha256, smlm_image_admin_pass,
                     sles15sp5_image_url, sles15sp5_image_sha256,
                     sles15sp6_image_url, sles15sp6_image_sha256]
```

## Roadmap

What is **not** shipped yet for Fleet. Full checklist: [ROADMAP Phase I](https://github.com/avaleror/rodeo-cli/blob/main/ROADMAP.md#phase-i--fleet--workshop-fan-out).

### F4 — Host-acquire

**Create KVM hosts from the laptop**, merge into `workshop.yaml`, then run the
normal converge loop. Providers stop at inventory; deploy / diagnose / retry stay
OpenSSH-only.

| Order | Provider | Status |
|-------|----------|--------|
| **F4a** | **AWS** (`boto3`, the `[aws]` extra: see [Install](install.md)) | **MVP shipped** — create/reuse by tags, wait running + SSH, write `hosts[]`, terminate tagged only |
| **F4b** | **GCP** (`google-cloud-compute`) | Planned |
| **F4c** | **Vultr Bare Metal** (`[vultr]` extra) | Planned — after GCP; real metal for nested KVM |
| **F4d** | **Hetzner Cloud** (`hcloud`) | Planned — after Vultr; nested KVM must be validated |

**Out of scope:** Equinix Metal (service sunset). Shared secrets across hosts.
Changing the nested phase engine for multi-host.

**MVP gaps (AWS):** no `plan` dry-run yet; `deprovision` does not rewrite `hosts[]`
(terminate only — edit or re-provision to refresh YAML); auto SG later.

```bash
pip install -e '.[aws]'        # once, in your rodeo-cli checkout (see install.md)
# AWS API creds (boto3 — never in YAML). Either:
#   ~/.aws/credentials  (+ optional AWS_PROFILE / ~/.aws/config)
#   or AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (+ optional AWS_SESSION_TOKEN)
# SSH: rodeo auto-manages ~/.rodeo/ssh/id_ed25519 and imports EC2 key pair "rodeo"
rodeo fleet provision -f workshop.yaml    # create/reuse → write hosts[]
rodeo fleet deploy -f workshop.yaml       # installs rodeo on each host, starts the lab
rodeo fleet doctor -f workshop.yaml       # needs rodeo on the host, so after deploy
rodeo fleet deprovision -f workshop.yaml --yes
```

Shared `HostProvider` Protocol and `provider:` YAML schema:
[Host-acquire design](#f4-host-acquire-design) below.

### Related (not Fleet-only)

Single-host `deployment_target: aws` (Phase E MVP) shares the same
`rodeo/providers/aws` adapter and `provider:` YAML schema as Fleet F4a.
On the EC2 guest the lab still runs as `baremetal`.

Live smoke checklists:

- [AWS single-host + fleet](examples/aws-fleet-claude-test-plan.md)
- [Testing — AWS live smoke](examples/testing.md#aws-live-smoke-i7i8xlarge--nvme)

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
  # ref: main                        # rodeo-cli git ref the hosts should run
  # install_url: https://…           # fork or air-gapped mirror of install.sh
defaults:
  ssh_user: ec2-user                 # AMI user (ec2-user / sles / root); non-root runs via sudo -n
  # identity_file: optional; default is the managed ~/.rodeo/ssh/id_ed25519
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
- Both run `rodeo` on the host, so they fail with `rodeo: command not found` until
  it is installed. On hosts from `rodeo fleet provision` that happens in
  `rodeo fleet deploy`, so run doctor after deploy. On BYO hosts that already ran
  `install.sh`, doctor works before deploy too.
- **doctor:** remote process exit is not enough — fleet also checks KVM, nested
  virt, core tools, and that a bundled profile fits RAM
  (`rodeo/fleet/doctor.py::_readiness_problems`).
- **status:** runs in `lab.dir` on each host. If `workshop.job.yaml` exists,
  status refreshes per-host job states for later retry.

---

## F2 — deploy, retry, access

### Instructor flow

```bash
rodeo fleet deploy -f workshop.yaml -j 4
rodeo fleet doctor -f workshop.yaml -j 8     # after deploy: it needs rodeo on the host
rodeo fleet status -f workshop.yaml          # poll until phases complete
rodeo fleet diagnose -f workshop.yaml        # pull logs for failed hosts
rodeo fleet retry -f workshop.yaml --failed-only
rodeo fleet access -f workshop.yaml --output json
```

### What `fleet deploy` does on each host

1. Ensure `rodeo` is on PATH (runs `install.sh` if missing — or always, with a ref;
   see [Which rodeo-cli the hosts run](#which-rodeo-cli-the-hosts-run)).
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

### Which rodeo-cli the hosts run

Hosts bootstrap themselves from GitHub — your local working tree never reaches
them. By default the bootstrap runs **only where `rodeo` is missing**, so a host
keeps the code it was first installed with; `--force` re-runs the *deploy*, not
the install.

That is fine for a workshop pinned to a release, and wrong when you have just
pushed a fix: a fleet-wide deploy would quietly run stale code on every host at
once. Pass a ref to force it:

```bash
rodeo fleet deploy -f workshop.yaml --ref main         # tip of main everywhere
rodeo fleet retry  -f workshop.yaml --ref feat/my-fix  # re-run the failures on a fix
```

With a ref the bootstrap runs every time and `install.sh --ref` hard-resets each
host's checkout to it. `lab.ref` sets the same thing in the inventory; `--ref`
overrides it. The installer is fetched from the same ref it checks out, unless
`lab.install_url` points somewhere explicit (fork, air-gapped mirror), which is
then used verbatim with the ref passed to it. An invalid ref fails at inventory
load, before any host is contacted.

This is the same mechanism as single-host `rodeo up --target aws --ref …` — both
paths share `rodeo/install_source.py`.

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

The access sheet is for the instructor. To give each student their own lab (link,
credentials, and reachability from any network), use the
[claim portal](#claim-portal-f5).

### Student network access (AWS)

A rodeo-managed security group only admits the operator's IP, so students cannot
reach their labs by default. To let them in from any network without knowing their
IP, set `provider.student_access: open` and open the lab UI ports once every lab is
up:

```yaml
provider:
  type: aws
  student_access: open     # operator (default) | open
```

```bash
rodeo fleet provision -f workshop.yaml     # ports stay operator-only
rodeo fleet deploy -f workshop.yaml
rodeo fleet status -f workshop.yaml        # wait until every host is complete
rodeo fleet open-access -f workshop.yaml   # 8443/30002 open to 0.0.0.0/0
rodeo fleet open-access --close -f workshop.yaml
```

`open-access` refuses unless every host has finished all phases and its Harvester
and Rancher admin passwords are strong (16+ characters with upper, lower and digit).
The check runs on each host and reports only strong / weak / missing; passwords never
leave the host. The security group is shared by the whole workshop, so one host that
is not ready blocks opening for all. Ports follow `lab.components`; SSH (`22`) always
stays operator-only. Any later `fleet provision` closes the ports again. With
`provider.security_group_ids` (BYO) rodeo changes nothing and asks you to open the
ports yourself.

### Claim portal (F5)

Students open one HTTPS page and enter the **workshop code** you give them (from
`portal info`, e.g. `RODEO-XVFD-20260930`; case, spaces and dashes do not matter).
Only then do they see the lab board and the claim form: name, email and a 6-digit
PIN of their choice (obvious ones such as 123456 or 111111 are refused). They get
their own lab: Harvester and Rancher URLs with the admin passwords and, optionally,
an SSH key for their lab host. You see who has which lab. Design:
[claim-portal.md](claim-portal.md).

Getting back to a lab:

1. **Same device:** the browser remembers the lab for 3 days; reopening the portal
   shows "Continue to your lab", even before the workshop code. "Not you? Forget this
   device" clears it on shared computers.
2. **Another device:** "Already have a lab? Get it back" asks only for email and PIN
   (the email finds the lab, the PIN proves it is theirs, so two students sharing a
   PIN is harmless). It returns the same lab under a new link; the old link stops
   working. It works while claiming is closed. Five wrong PINs lock the email.
3. **Anything else:** you have every lab on the instructor page, and
   `rodeo fleet portal unlock EMAIL` clears a lockout.

Privacy: every page has a footer line ("we only keep your name and email for this
workshop, deleted when it ends; essential cookies only") linking to `/privacy`, and
the claim form repeats it where data is entered. `/privacy` (public, no code needed)
explains what is kept, for how long, and the three cookies (`csrf`, `access`, `lab`);
its durations come from the same constants that set the cookies, and a test fails if
they drift. Have your privacy team review the wording before a public event.

```yaml
provider:
  type: aws
  student_access: open        # required: students must reach their labs
portal:
  enabled: true
  mode: open                  # open (workshop code + email) | roster (invite links) | both
  # roster: students.csv      # roster / both: CSV with name,email[,host_id]
  student_ssh: true           # per-lab `student` user + key, own sshd on :2222 (key-only)
  title: SUSE Virtualization workshop
  guide_url: https://avaleror.github.io/suse-virt-workshop/   # or /guide/ if served by the portal
  # code_letters: 6           # random letters in RODEO-XXXXXX-YYYYMMDD (4-8; 6 = 191M codes)
  # hostname: labs.example.com   # default portal-<ip>.sslip.io (Let's Encrypt)
  # instance_type: t3.small
```

```bash
rodeo fleet provision -f workshop.yaml     # labs + a small portal VM (own SG: 443/80 open, 22 operator)
rodeo fleet portal up -f workshop.yaml     # portal service + Caddy with a real TLS certificate
rodeo fleet deploy -f workshop.yaml
rodeo fleet open-access -f workshop.yaml   # once every lab is complete (adds :2222 with student_ssh)
rodeo fleet portal publish --watch -f workshop.yaml  # follow the deploy on the instructor page;
                                           # each lab is published the moment it is ready
rodeo fleet portal info -f workshop.yaml   # URL + workshop code for the slide
rodeo fleet portal status -f workshop.yaml # who has which lab
rodeo fleet portal admin-link -f workshop.yaml  # instructor page (all lab credentials; do not project)
rodeo fleet deprovision --yes -f workshop.yaml  # labs + portal (--keep-portal to keep it)
```

| Instructor command | What |
|--------------------|------|
| `portal publish [--watch] [--interval 60]` | Push labs to the portal. With `--watch`: deploy progress per lab (phases done, current phase, elapsed, failures) on the instructor page, and each lab becomes claimable as soon as it is ready. Keep the laptop awake ([install.md](install.md#differences-and-things-to-know)) |
| `portal status` / `export [-o file.csv]` | Claims: lab, name, email, how, when claimed, first opened |
| `portal admin-link` | New secret link to the instructor page: every claim plus every lab's URLs, passwords and SSH key (the previous link stops working) |
| `portal open` / `close` / `rotate-code` | Claim window; a new workshop code (logs everyone out of the board; personal lab links keep working) |
| `portal release LAB` / `revoke EMAIL` / `reassign EMAIL LAB` / `unlock EMAIL` | Fix mistakes during the workshop |
| `portal invite [--rotate]` | Roster mode: reserve labs, write `<workshop>-invites.csv` (0600) with personal links |

What protects what:

- **The portal is passive.** It never connects to lab hosts or cloud APIs and holds no
  rodeo SSH key and no cloud credentials. The laptop pushes lab records to it over SSH,
  on stdin. The portal code is `rodeo/portal/` (standard library only), copied verbatim
  and run under a hardened systemd unit as an unprivileged user.
- **Workshop code first.** The portal's hostname is public within minutes (TLS
  certificates are logged in Certificate Transparency), so the front page shows
  nothing but a code field until the code is entered. After that a cookie (bound to
  the current code, 24 h) shows the board of every lab (free / claimed / building)
  with the claimant's *name*, never their email or any credential, and the claim
  form. The default code has 6 random letters (about 191M codes). Wrong codes are
  also capped across all addresses together (200 per 10 minutes), so even many
  addresses at once get at most 9,600 guesses in an 8-hour workshop: about 0.005%
  odds. Set `portal.code_letters` (4-8) to change the length, and `rotate-code` if a
  code leaks. After upgrading, the next `portal up` on an existing portal rotates its
  code to the new length (set `code_letters: 4` to keep the old one).
- **Workshop guide link:** with `portal.guide_url` every student page (claim page, and
  their lab page above the credentials) links to the exercises: an `https://` URL such as
  GitHub Pages, or a path served by the portal itself. The guide stays on GitHub Pages
  by default because the portal is destroyed with the fleet, and students keep the guide
  after the workshop. Change it with `portal up` at any time; claims are kept.
- **The instructor page**, behind a secret link, shows emails, deploy progress and
  every lab's credentials.
- **Claims:** personal links are 24 random bytes stored as SHA-256, PINs as scrypt,
  5 wrong PINs lock an email, CSRF on every form, no-store and CSP headers. Only
  *failed* attempts (wrong code or PIN, unknown links) count against a limit of 30 per
  10 minutes per address, so a whole classroom behind one NAT address can claim.
  Wrong workshop codes also count against 200 per 10 minutes for the whole portal.
  When that is hit, only people who haven't entered the code yet wait a few minutes;
  everyone who has keeps their 24 h access.
  Logs never contain tokens, emails, codes, PINs or passwords.
- **Student SSH:** a `student` user per lab host, key-only, with its own key generated
  on your laptop (`~/.rodeo/fleet/<workshop>/student-keys/`). Publish proves on every
  host that the user has no sudo, is in no privileged group and cannot read `/root`,
  where the fleet-wide rodeo key lives; any failed check aborts. Students never get
  root on a lab host.
- `publish` is safe to re-run (after a `fleet retry`, or when a lab finishes later):
  labs that are not ready are shown to students as "still building" and are never
  assigned.

---

## OpenSSH requirements

- Key-based auth with `BatchMode=yes` (no password prompts).
- Host keys are trusted on first use and then pinned per workshop
  (`StrictHostKeyChecking=accept-new`, `~/.rodeo/fleet/<workshop>/known_hosts`), because
  fleet sends lab passwords and student keys over these connections. Provisioning
  forgets the key of every address it creates (EC2 reuses public IPs). If a host is
  rebuilt outside rodeo and SSH reports a changed host key, remove it with
  `ssh-keygen -R <ip> -f ~/.rodeo/fleet/<workshop>/known_hosts`. Host→VM connections
  inside a lab (`rodeo/ssh.py`) still skip verification: those VMs are recreated all
  the time on a private network.
- `ssh` on the laptop PATH; Agent / `ProxyJump` / `identity_file` work as usual.
- On each remote: `rodeo` + `tmux` on PATH for the SSH user; typically `root@`.

Fleet does **not** sudo-re-exec on the laptop.

## Design notes

- `rodeo/ssh.py` = host→VM lab connections.
- `rodeo/fleet/ssh_exec.py` = laptop→KVM host.
- Concurrency defaults: doctor/status `-j 8`; deploy/retry use `lab.concurrency`
  (default 4) unless `-j` is set. Prefer low concurrency for deploy (ISO/network).
- See [Roadmap](#roadmap) for F4 host-acquire. Equinix is out of scope.
  Shared secrets and changing the phase pipeline stay out of Fleet.

---

## F4 host-acquire design

Shared Protocol / schema for host-acquire. **AWS (F4a) MVP is implemented** in
`rodeo/providers/` for both Fleet and single-host `deployment_target: aws`.
GCP / Vultr / Hetzner remain stubs. Summary in [Roadmap](#roadmap).

**Important — never set `lab.target: aws` in `workshop.yaml`.**

AWS-provisioned workshop hosts run the lab as `lab.target: baremetal` (full
firewalld / DNAT / `finalise`). `deployment_target: aws` is the laptop
control-plane / plan host-context marker (`rodeo up --target aws` and the plan
kept on the instance for disk floors + `destroy --cloud`). Mixing
`lab.target: aws` into Fleet inventory breaks phase behaviour.

| Axis | Single-host | Fleet workshop |
|------|-------------|----------------|
| Acquire | `rodeo up --target aws` + `provider:` in plan | `rodeo fleet provision` + `provider:` in `workshop.yaml` |
| Lab topology profile | `--profile harvester` | same base profile seeded on each host |
| Execution on the KVM host | phases as `baremetal` | `lab.target: baremetal` |
| Plan marker on the host | keep `deployment_target: aws` | do **not** set `lab.target: aws` |

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
- `provider.student_access` ∈ `{operator, open}` when set (default `operator`); see [Student network access](#student-network-access-aws).
- `provider.count` integer 1–64 when set; if `hosts:` non-empty and count omitted, ensure exactly those ids (reuse/create by `rodeo-host-id`).
- SSH identity is managed under `~/.rodeo/ssh/id_ed25519` (auto-created; imported to EC2 as key pair `rodeo`). `defaults.identity_file` / `provider.key_name` are optional.
- Type-specific required keys enforced by that adapter’s `validate()` only.

#### AWS API credentials

Provision uses **boto3 only** (AWS CLI is optional). Supply credentials via either:

| Method | How |
|--------|-----|
| Shared file | `~/.aws/credentials` (and optional `~/.aws/config` / `AWS_PROFILE`) |
| Environment | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (+ optional `AWS_SESSION_TOKEN`) |

Never put AWS access keys in `workshop.yaml` or `rodeo-plan.yaml`.

#### `provider.type: aws` (F4a)

Prefer **`i7i.8xlarge`** (local NVMe) for Harvester / Edge I/O. Metal remains valid.
`apply_host_context` raises `resources.harvester.disk_gb` to a flat **500 GB**
and `resources.rancher.disk_gb` to **60 GB** — never scaled by node count, so
the rest of the NVMe device is deliberately left free — and mounts NVMe on
`image_dir`. Root `volume_size_gib` only needs the OS (~100 GiB). Tiny /
burstable types are rejected at validate. Nested virt defaults **on** for
non-metal types.

SSH: rodeo generates `~/.rodeo/ssh/id_ed25519` if missing, imports it as EC2 key pair
**`rodeo`**, and plants the same private key on the KVM host so nested VMs share it.
New instances also get cloud-init UserData: root authorized_keys + `NOPASSWD` sudo
for `ssh_user` so remote `rodeo up` never asks for a password.
Use `rodeo ssh student-01` or `rodeo ssh student-01/rancher`.

```yaml
provider:
  type: aws
  count: 12
  region: eu-central-1              # required
  instance_type: i7i.8xlarge        # preferred: local NVMe; metal also OK
  # ami omitted → newest openSUSE Leap 16.0 (x86_64) Marketplace AMI
  # ami_name_filter: "openSUSE Leap 16.0 (x86_64)*"   # default when ami unset
  # ami: ami-0123456789abcdef0      # optional pin (SLES 16 / specific Leap build)
  subnet_id: subnet-0abc…           # required
  security_group_ids:               # must allow 22, 8443, 30002 as needed
    - sg-0abc…
  # key_name: rodeo                 # default; ImportKeyPair managed by rodeo
  # associate_public_ip: true       # default true
  # nested_virtualization: true     # default on for non-metal
  # volume_size_gib: 100            # root EBS; lab disks use NVMe via host_context
```

**`security_group_ids` is optional, but set it explicitly for a real fleet.**
Omit it and rodeo auto-manages one, scoped to *the machine running `fleet
provision`'s* current public IP — right for single-host `rodeo up --target
aws` (you're both operator and the only person who needs in), wrong for a
multi-attendee fleet where each student connects from their own IP: they'd
all be locked out except you. Set `security_group_ids` to an SG that actually
covers your attendees' network (a classroom CIDR, `0.0.0.0/0` for a public
workshop, or a VPN range) whenever `count` > 1 real students.

Subscribe once to [openSUSE Leap on Marketplace](https://aws.amazon.com/marketplace/pp/prodview-wn2xje27ui45o)
(current build example: *openSUSE Leap 16.0 (x86_64) - v20260629*). SSH user: **`ec2-user`**.
SLES 16 works too — set `ami:` to that image id and `ssh_user` if it is not `ec2-user`.

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
