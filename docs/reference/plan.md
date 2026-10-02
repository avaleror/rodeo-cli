# rodeo-plan.yaml reference

`rodeo-plan.yaml` is the resource and deployment configuration for a lab. It answers: how big are the VMs, where are they stored, what are the credentials, and what deployment target is this running on?

It does **not** describe topology (nodes, network, services). That lives in [`definition.yaml`](definition.md).

Both files together form a **profile** — the complete description of a lab that `rodeo up`, `plan`, and `deploy` consume.

---

## Full annotated example

```yaml
# The pipeline this plan uses. Determines which phases run.
# suse-virt: Harvester HCI (with or without Rancher)
# rancher:   Rancher Prime on K3s only (no Harvester, no PXE)
type: suse-virt

# Used as the libvirt object prefix, state file name, and log label.
# Must be unique per host if you run multiple labs.
name: my-lab

# Where this lab runs. Controls whether the finalise phase is deferred,
# and (for aws) whether the laptop provisions an EC2 host then remote-deploys.
# baremetal: full deploy including VM autostart on host reboot
# instruqt:  skips finalise until you run `rodeo deploy --from finalise --finalise`
# aws:       laptop control plane — provision EC2, then remote `rodeo up --target baremetal`
deployment_target: baremetal

# Virtual machine resource allocation.
# These are the per-VM values. total host RAM = (harvester nodes × memory_mib) + rancher.memory_mib.
resources:
  harvester:
    memory_mib: 16384   # RAM per Harvester node in MiB (16 GiB)
    vcpu: 8             # vCPUs per Harvester node
    disk_gb: 320        # disk per Harvester node — do not go below ~250 (see note)

  rancher:
    memory_mib: 8192    # RAM for the Rancher VM in MiB (8 GiB)
    vcpu: 4             # vCPUs for Rancher
    disk_gb: 60         # disk for the Rancher VM

# Credentials. Values starting with ?? are resolved at deploy time — never hardcoded here.
#
# ?? resolution sources (in order of preference):
#   ??key            → ~/.rodeo/secrets.yaml key named "key"
#   ??env:VAR_NAME   → environment variable VAR_NAME
#   ??file:/path     → contents of a file
#   ??cmd:command    → stdout of a shell command (trimmed)
#
# rodeo up and rodeo init generate ~/.rodeo/secrets.yaml automatically.
# Deploy fails immediately if any ?? placeholder cannot be resolved.
credentials:
  harvester_os_password: "??harvester_os_password"     # OS login for Harvester nodes (rancher user)
  harvester_admin_password: "??harvester_admin_password" # Harvester web UI admin password
  rancher_admin_password: "??rancher_admin_password"   # Rancher web UI admin password
  harvester_token: "??harvester_token"                 # RKE2 cluster join token (internal)

# Network settings at the plan level. Most network config lives in definition.yaml.
# Override here only if your host uses a non-default VIP or gateway.
network:
  vip: 192.168.122.10     # Harvester cluster VIP (kube-vip floating IP)

# Storage configuration for the host.
storage:
  device: ""                          # "" = use the default single-disk path
                                      # "/dev/nvme1n1" = dedicate a second disk
  image_dir: /var/lib/libvirt/images  # where VM disks and ISOs are stored

# Software versions. Pin these to reproduce a specific lab.
versions:
  harvester: "1.8.1"          # Harvester ISO version to download and install
  rancher: "2.14.1"           # Rancher Prime Helm chart version
  k3s: "v1.35.3+k3s1"        # K3s version for the Rancher VM
  cert_manager: "v1.16.2"     # cert-manager Helm chart version (Rancher dependency)

# Jinja2 templating: define variables used in this file.
# Values can be overridden with -P or --paramfile at deploy time.
parameters:
  harvester_mem: 16384

# Then use them:
# resources:
#   harvester:
#     memory_mib: {{ harvester_mem }}
```

---

## Field reference

### `type`

| Value | Pipeline | Phases |
|-------|----------|--------|
| `suse-virt` | Harvester HCI (with or without Rancher) | `kvm_host` → `vms` → `pxe_server` → `cluster` → `rancher` → `finalise` |
| `rancher` | Rancher Prime on K3s | `kvm_host` → `vms` → `boot` → `rancher` → `finalise` |
| `suse-edge` | SUSE Edge 3.6 (Rancher + Elemental + EIB + edge nodes) | in development on `feature/suse-edge` |

**Required.** No default.

### `name`

String. Used as the prefix for all libvirt objects (`<name>-harvester1`, etc.), the state file name (`~/.rodeo/state/<name>.yaml`), and log labels.

Must be unique per host. Changing `name` after deploy creates orphaned resources — run `rodeo clean` first.

### `deployment_target`

| Value | Behaviour |
|-------|-----------|
| `baremetal` | Full deploy. `finalise` enables VM autostart on host reboot. |
| `instruqt` | `finalise` is skipped automatically. Run it after the Instruqt snapshot: `rodeo deploy --from finalise --finalise` |
| `aws` | Laptop control plane: require a `provider:` block, provision/reuse one EC2 KVM host, wait for SSH, remote-run `rodeo up` on that host. Tear down with `rodeo destroy --cloud --yes`. BYO: create the instance yourself, keep `deployment_target: aws` (or set `storage.backend: nvme`) and deploy on the box. |

**Required.** Defaults to `baremetal` when omitted. Plugins can add targets via
`host_context.register_host_context()` (see the architecture doc's extension
points) — a registered target is accepted here, by `--target`, and by the
`rodeo up` prompt.

On **instruqt**, `rodeo up` / lab seeding also applies host-aware `resources` presets so
Σ guest vCPU stays near ~70% of the builder (Harvester typically 6–8 vCPU / 20 GiB).
Existing plans are not rewritten on re-deploy; only seeded plans get the presets.
`rodeo doctor` / `rodeo deploy --check` warn (non-fatal) when a plan still exceeds the budget.

On **aws**, `apply_host_context()` (seed + deploy) raises `resources.harvester.disk_gb`
to a flat **500 GB per Harvester node** and `resources.rancher.disk_gb` to **60 GB**
(never scaled by node count — the rest of the NVMe device is deliberately left free,
matching real-world/Instruqt sizing). Also sets `storage.backend: nvme` and mounts the
largest non-root NVMe on `image_dir`. Nested virt is enabled by default on non-metal
types.

**Option A — one topology profile, AWS is where.** Use the base profile and
`--target aws` (there is no separate `*-aws` plan template):

```bash
rodeo up --yes --profile harvester --target aws --instance-tier recommended
```

Instance size comes from `rodeo/providers/instance_catalog.py` keyed by that
**same** profile name. **recommended** for `harvester` and `harvester-2n` is
**`m8id.8xlarge`** (32 vCPU / 128 GiB / a single ~1.9 TiB NVMe device — unlike
`i7i.8xlarge`, which splits its NVMe across two devices and rodeo only mounts
one). `suse-edge` recommended is **`m8id.4xlarge`**. Explicit
`provider.instance_type` still wins.

Keep `deployment_target: aws` in the plan on the instance (host-context +
`destroy --cloud` marker). On the EC2 box itself, phases run as **baremetal**.
Never set `lab.target: aws` in Fleet `workshop.yaml` — use `lab.target: baremetal`
there (see [Fleet](../fleet.md)).


### `provider` (when `deployment_target: aws`)

Same shape as Fleet [`workshop.yaml` provider](../fleet.md#workshopyaml-provider-schema).
Required fields for AWS: `type`, `region`, `subnet_id`, and either
**`instance_type`** or **`instance_tier`**.

**Security group is auto-managed unless you pin one.** Omit
`security_group_ids` and rodeo creates (or reuses) an SG named `rodeo-<name>`
in the subnet's VPC, opens exactly the ports the host needs — SSH (22),
Harvester UI (8443), Rancher NodePort (30002) — and scopes all three to
*this machine's current public IP*, detected automatically. Re-running from
a different IP (new wifi, VPN toggled) updates the rule in place rather than
piling up stale ones. `rodeo destroy --cloud --yes` deletes it once nothing
else tagged for the workshop is still running (best-effort: if the instance
hasn't finished detaching yet, re-run destroy — it's never left as a hard
failure). Set `security_group_ids` explicitly to opt back into a hand-managed
SG — useful for a shared multi-attendee IP range, a bastion topology, or an
SG your org's security policy already owns.

**Instance size (single-host v1):** pick one of three tiers for the lab profile, or set
an explicit type. `rodeo up --target aws` prompts interactively when neither is set;
with `--yes` it uses **recommended**.

| Tier | Meaning |
|------|---------|
| `budget` | Meets the profile minimum (often EBS-backed Nitro) |
| `recommended` | Preferred size for that profile (often local NVMe; see `instance_catalog`) |
| `performance` | Metal / larger escape hatch |

Before create, rodeo checks the type is **offered in the region** and probes capacity
via `RunInstances` DryRun. If the region has no capacity, it fails with a message to
try another region or tier — it does not silently downsize.

```yaml
deployment_target: aws
provider:
  type: aws
  region: eu-central-1
  # Either:
  instance_tier: recommended          # budget | recommended | performance
  # Or pin explicitly:
  # instance_type: i7i.8xlarge
  # ami omitted → newest suse-sles-16-0-v<date>-hvm-ssd-x86_64 (SLES 16 PAYG)
  # ami: ami-…                        # optional pin
  # ami_name_filter: "suse-sles-16-0-v????????-hvm-ssd-x86_64"
  subnet_id: subnet-…
  # security_group_ids: [sg-…]        # omit → rodeo creates/manages one,
  #                                    # scoped to this machine's public IP
  ssh_user: ec2-user                  # SLES 16 / Leap default
  # nested_virtualization: true       # default on for non-metal
  # volume_size_gib: 100              # root EBS; lab disks use NVMe
  # ref: main                         # rodeo-cli git ref to run on the host
  # install_url: https://…/install.sh # fork or air-gapped mirror
```

```bash
rodeo up --yes --profile harvester --target aws --instance-tier recommended
```

**Which rodeo-cli the host runs (`--ref` / `provider.ref`).** The instance
bootstraps itself with `install.sh` from GitHub — your local working tree
never reaches it. By default the bootstrap runs **only when `rodeo` is
absent**, so a host stays on the code it was first installed with, the same
way `clean --refresh` refuses to move a pinned host's version unasked.

Pass a ref to change that:

```bash
rodeo up --target aws --ref main         # pick up commits pushed since bootstrap
rodeo up --target aws --ref v0.15.0      # pin a release
rodeo up --target aws --ref feat/my-fix  # test a branch on a real host
```

A ref makes the bootstrap run **every time** and hard-resets the host's
checkout to it (`install.sh --ref`), which is the only way a just-pushed
commit reaches an existing host. The installer itself is fetched from the same
ref, so `install.sh` and the code it installs cannot disagree. An explicit
`provider.install_url` is used verbatim — for a fork or an air-gapped mirror —
and the ref is still passed to it. `--ref` beats `provider.ref`; an invalid
ref is rejected **before** any instance is launched, so a typo costs nothing.
`--ref` applies only when the laptop is the AWS control plane; anywhere else
it warns and is ignored.

To test a fork without editing plans, set environment variables where `rodeo`
runs:

| Variable | Effect |
|---|---|
| `RODEO_REPO` | git URL `install.sh` clones and updates from; passed on to remote bootstraps. A GitHub URL also points the default install URL at that repository's `install.sh` |
| `RODEO_INSTALL_URL_TEMPLATE` | install.sh URL with a `{ref}` placeholder, for non-GitHub mirrors |

A configured `install_url` still wins over both. Fleet has the same mechanism —
[`fleet deploy --ref` / `lab.ref`](../fleet.md#which-rodeo-cli-the-hosts-run).

**AWS API credentials** (boto3 — never in the plan): `~/.aws/credentials` /
`AWS_PROFILE`, **or** `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`
(+ optional `AWS_SESSION_TOKEN`). AWS CLI is optional.

**AMI choice matters more than it looks.** The default is SLES 16
**pay-as-you-go** (published by SUSE via Amazon), and it is not
interchangeable with the alternatives:

- **openSUSE Leap 16** is a Marketplace product whose listing does not permit
  7th-generation Intel instance types. Nested virtualisation needs exactly
  those, so Leap cannot run a nested lab on anything but `*.metal`.
- **SLES BYOS** images allow `i7i`, but ship without a subscription, so zypper
  has no repos and `install-deps` fails. rodeo does not register hosts.

PAYG avoids both traps at a licence surcharge of roughly $0.125/hr on
`i7i.8xlarge`. No Marketplace subscription or opt-in is needed.

**SSH / root:** new instances get cloud-init UserData that installs the managed
pubkey for **root** and **passwordless sudo** for `ssh_user` (`ec2-user` on SLES 16).
Remote `rodeo up` runs under `sudo -n` so it never prompts. `rodeo ssh primary`
or `rodeo ssh primary/rancher` use the same managed key.

### `resources`

Per-VM sizing. All fields are per-node (not total).

| Field | Unit | Minimum | Notes |
|-------|------|---------|-------|
| `memory_mib` | MiB | 12288 (12 GiB) for Harvester | Harvester requires at least 12 GiB; 16 GiB recommended |
| `vcpu` | threads | 4 | On hyper-threaded hosts, each thread counts |
| `disk_gb` | GiB | **320 for Harvester** | Elemental's persistent partition is a fixed 150 GiB floor regardless of disk size; below ~250 it starves and containerd fails; the rest goes to Longhorn's own partition, so bigger leaves Longhorn more room |

The `rancher` block is only used when the topology includes a Rancher VM (see `definition.yaml`).

### `credentials`

All credential values must use `??` placeholders. Plain text passwords in plan files are rejected by `validate_config()`.

| Key | Used for |
|-----|----------|
| `harvester_os_password` | OS login on Harvester nodes (`rancher` user SSH + console) |
| `harvester_admin_password` | `admin` user in the Harvester web UI |
| `rancher_admin_password` | `admin` user in the Rancher web UI |
| `harvester_token` | RKE2 cluster join token — internal; do not share with attendees |

`rodeo up` and `rodeo init` generate random values and write them to `~/.rodeo/secrets.yaml` (chmod 600). Never commit that file.

### `network`

| Field | Default | Notes |
|-------|---------|-------|
| `vip` | `192.168.122.10` | Harvester cluster VIP. Must be in the same `/24` as nodes but outside the DHCP range. |

Most network topology (CIDR, gateway, DNS domain, per-node IPs) is in `definition.yaml`.

### `storage`

| Field | Default | Notes |
|-------|---------|-------|
| `device` | `""` | Leave empty for single-disk hosts. Set to `/dev/nvme1n1` (or similar) to dedicate a second disk. Confirm with `lsblk`. |
| `image_dir` | `/var/lib/libvirt/images` | Where libvirt stores VM disks and ISOs. Must have 600–900 GiB free for a full lab. |

### `libvirt`

| Field | Default | Notes |
|-------|---------|-------|
| `uri` | `qemu:///system` | libvirt connection URI. |
| `disk_cache` | target-dependent | Guest qcow2 `cache=` mode. Default `none` on baremetal, `writeback` when `deployment_target: instruqt` (better nested cloud I/O). Allowed: `none`, `writethrough`, `writeback`, `unsafe`, `directsync`. |
| `disk_io` | target-dependent | Guest qcow2 `io=` mode. Default `native` on baremetal, `threads` on Instruqt. Allowed: `native`, `threads`, `io_uring`. |

Override at deploy time without editing the plan:

```bash
rodeo deploy -P libvirt.disk_cache=writeback -P libvirt.disk_io=threads
```

### `versions`

| Field | Default | Notes |
|-------|---------|-------|
| `harvester` | `"1.8.1"` | ISO version string. rodeo downloads the matching ISO from the Harvester release page. |
| `rancher` | `"2.14.1"` | Rancher Prime Helm chart version. |
| `k3s` | `"v1.35.3+k3s1"` | K3s version installed on the Rancher VM (`suse-virt` / `rancher`). SUSE Edge defaults to `v1.35.5+k3s1`. |
| `cert_manager` | `"v1.16.2"` | cert-manager Helm chart (Rancher dependency). |

### `parameters`

Optional map of Jinja2 template variables. Use to avoid repeating values across the file. Override at deploy time with `-P key=value` or `--paramfile overrides.yaml`.

---

## CLI overrides

You can override any plan value at deploy time without editing the file:

```bash
# Single value
rodeo deploy -P resources.harvester.memory_mib=20480

# Multiple values
rodeo deploy -P resources.harvester.vcpu=10 -P versions.harvester=1.5.0

# From a file (deep-merged over the plan)
rodeo deploy --paramfile big-lab.yaml
```

**Precedence (lowest to highest):** profile defaults → `rodeo-plan.yaml` → `--paramfile` → `-P`

---

## story — workshop narrative, languages, and variants

`rodeo story render` produces the participant hand-out for this lab from
rmstory-tagged markdown in the lab's `story/` directory:

```
<lab>/story/*.md       tagged markdown sources (authored in English)
<lab>/story/stories/   story-variant indexes, one <id>.yaml per variant
<lab>/story/strings/   translation store (rmstory filesystem backend)
```

The optional `story:` block sets the defaults (CLI flags override):

```yaml
story:
  language: es          # target language (default en = source, no translation)
  id: villain-arc       # story variant to assemble (default: all spans)
  engine: gemini        # rmstory engine to machine-fill missing translations
  engine_env:           # engine credentials — ?? secrets are resolved
    GEMINI_API_KEY: "??gemini_api_key"
```

Deployment facts are substituted into the rendered text as Jinja expressions —
write them inside invariant spans so translation never touches them:
`<span no>{{ rancher_url }}</span>`. Available facts: `name`, `type`,
`language`, `vip`, `harvester_url`, `rancher_ip`, `rancher_url`,
`rancher_nodeport`, `dns_domain`, `gateway`, `vms`, `vm_names`, `credentials`.

Rendering in the source language with no variant needs nothing installed;
translation and variant assembly use the `rmstory` system package
(`sudo rodeo install-deps --story` — distro packages, never PyPI).

The post-deploy success screen uses the same machinery: each profile ships a
tagged `success.md`, a lab can override it with `story/success.md`, and
`story.language` localizes it. Translation problems always degrade to the
English source — the payoff screen never fails.

---

## lab_in_a_box — deploying with, or exporting to, lab-in-a-box

[lab-in-a-box](https://github.com/SUSE-Technical-Marketing/lab-in-a-box) builds
VMs and runs addons (SUSE Multi-Linux Manager, Uyuni, NeuVector, ...) from a
`lab.json`. rodeo renders that file from the plan and definition, which stay
the source of truth, in two ways:

- `type: lab-in-a-box` deploys through it. rodeo prepares the host (packages,
  libvirt network and DNS, firewall forwards for `exposed_services`, base
  images, secrets), installs a pinned lab-in-a-box, and runs its `setup_lab.py`.
  The phases are `kvm_host`, `labinabox_host`, `labinabox` and `custom_scripts`.
  `rodeo clean` runs its `destroy_lab.py`.
- `rodeo export --format lab-in-a-box` only writes the `lab.json`, for a
  lab-in-a-box automation node you run yourself.

The `lab_in_a_box:` block holds the knobs that exist only on the lab-in-a-box side:

```yaml
lab_in_a_box:
  source:                               # type: lab-in-a-box only — which lab-in-a-box to run
    repo: https://github.com/SUSE-Technical-Marketing/lab-in-a-box
    ref: 698506e6a40d495e303a276bfa3f0aa3912bf504   # branch, tag or SHA
  parallel: 4                           # setup_lab.py --parallel=N
  root_password: "??universal_pwd"      # root password of every VM
  images:                               # base images: become ISO_URL/ISO_SHA256[_URL] in lab.json;
                                        # lab-in-a-box downloads + verifies them into ISO_LOC
    - name: ubuntu-24.04-server-cloudimg-amd64.img
      url: https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img
      sha256_url: https://cloud-images.ubuntu.com/releases/24.04/release/SHA256SUMS   # or sha256: <hex>
  nodes:                                # per-node lab.json keys, by short node name
    ubuntu2404lts: {ISO_IMAGE: ubuntu-24.04-server-cloudimg-amd64.img, ssh_pwauth: "true"}
    smlm: {ISO_IMAGE: smlm.qcow2, addons: [smlm]}
  iso_image: openSUSE-Leap-15.6.qcow2   # common base image for nodes without their own
  config_method: cloud-init             # cloud-init (default) | virt_customize | "" (ignition/combustion)
  cluster_name: mgmt                    # kcluster name (also its DNS record: <name>.<domain>)
  cluster_type: k3s                     # k3s (default) | rke2
  clu_rel: stable                       # install channel — exact version pins don't carry over
  addons: [rancher]                     # override the derived kcluster install_<addon> list
  sections:                             # verbatim lab.json sections (addon config)
    smlm: {smlm_deployment: podman, smlm_admin_pass: "??smlm_admin_password"}
```

**Variants:** `lab_in_a_box.variants.<name>` overlays the block, and
`lab_in_a_box.variant` picks one (`-P lab_in_a_box.variant=<name>`). In an
overlay, dicts merge, `null` deletes a key, and `images` merge by name
(`remove: true` drops one). A variant can add `operator_secrets` and a `notice`
logged at deploy. Only the selected variant's secrets are asked for.
`smlm-workshop` uses this to switch between booting its pre-built server
(`image`) and building it from stock SLES (`scratch`).

An image's `url` can also be a list of mirrors, tried in order and all checked
against the same checksum.

**Secrets:** every credential is a `??key`. `rodeo up` generates any plain
`??key` the plan references that `~/.rodeo/secrets.yaml` lacks. Keys listed
under a top-level `operator_secrets:` are the exception: those are values only
you have, such as an SCC regcode or a pre-built image's password, and `rodeo up`
asks for them instead (under `--yes` it stops and says which ones to add). The
rendered `lab.json` holds the resolved values, so it is written `0600` to
`<lab>/.labinabox/`. The deploy stops if any `??` value is still unresolved.

To test another lab-in-a-box without editing the plan, set
`RODEO_LABINABOX_REPO` and/or `RODEO_LABINABOX_REF`; each takes precedence over
the matching `source:` key. For a local lab-in-a-box checkout, set
`RODEO_LABINABOX_PATH=/path/to/lab-in-a-box`; it takes precedence over all of them.
These variables are read on the host that runs `rodeo up`.

**Existing lab-in-a-box host:** by default (`target.mode: auto`) rodeo installs
lab-in-a-box on the host it runs on. It uses an existing one instead when that
host already has lab-in-a-box set up (a `/etc/lab_creation.cfg` rodeo didn't
write), or when `target.host` names a remote automation node:

```yaml
lab_in_a_box:
  target:
    mode: auto            # auto | managed | existing
    host: automation.example.lab   # remote lab-in-a-box host (rodeo's key in its authorized_keys)
    ssh_user: root
    libvirt_uri: qemu+ssh://root@kvm1.example.lab/system   # its hypervisor, for status/start/stop
```

With an existing lab-in-a-box, rodeo skips `kvm_host` and leaves that host's
install, network, firewall and keys alone. The lab has to fit that host's own
network: set `network.bridge`/`cidr`/`gateway`/`domain` in `definition.yaml`,
and anything else under `sections.common` (e.g. `mydns`).

A remote host gets `lab.json` over SSH stdin into `~/.rodeo-labs/<plan>/` (mode
0600, with an owner marker). `setup_lab.py` runs there and its output streams
back. `rodeo clean` runs `destroy_lab.py` there, but only for a lab carrying this
plan's marker. `rodeo ssh <vm>` hops through the host.

Every deploy, in either mode, first checks `lab.json` against the installed
lab-in-a-box's own schema (`lab_schema`) and stops, naming the fields, if that
lab-in-a-box is too old for the plan.

**VMs in a cloud account instead of nested KVM:** with `lab_in_a_box.cloud`,
lab-in-a-box creates every node in a cloud account through its own compute
backends: aws, gcp, hetzner, alibaba, scaleway, upcloud, ovhcloud, exoscale. The
rodeo host then only runs lab-in-a-box, so it needs no KVM (a laptop or a small
VM will do):

```yaml
lab_in_a_box:
  iso_image: ami-0123456789abcdef0      # a provider image (AMI, image name/ID), per region
  cloud:
    cloudtype: aws
    account: aws-lab                    # credential file name — one per lab (clean removes it)
    settings:                           # the backend's keys, as in lab-in-a-box's README
      AWS_REGION: eu-north-1
      AWS_ACCESS_KEY_ID: "??aws_access_key_id"
      AWS_SECRET_ACCESS_KEY: "??aws_secret_access_key"
  nodes:
    vm1: {cloud_instance_type: t3.large}
```

rodeo writes the account to `/etc/lab_creation/credentials/<account>.yaml` (0600).
Any `??key` in `settings` is always asked for, never generated. Nodes get no static
IP/MAC: the provider assigns them. rodeo reads the addresses back after the deploy,
so `rodeo ssh <vm>` and the success screen use them.

Leave out `settings` to use an account that already exists on the lab-in-a-box
host, e.g. an encrypted one made with `setup_credentials.py`.

`rodeo status`/`start`/`stop`/`restart` ask lab-in-a-box's `vm_power.py`, on every
provider.

`exposed_services` become each target VM's `open_ports`. lab-in-a-box opens them
with the provider's own mechanism:

| Provider | How the port is opened |
|---|---|
| aws | security-group rules (needs `AWS_SECURITY_GROUP_ID`) |
| gcp | a firewall rule plus a network tag on the VM |
| alibaba | rules on `ALIBABA_SECURITY_GROUP_ID` |
| exoscale | a per-VM security group |
| hetzner, scaleway, upcloud, ovhcloud | nothing to open: they let inbound traffic in by default, unless you added your own firewall |

**Several instances on one host:** a top-level `instance: N` (1–99; 0 is the
plan as written) moves a lab-in-a-box lab out of the way of other copies:

| | instance 0 | instance N |
|---|---|---|
| libvirt network / bridge | `default` / `virbr0` | `rodeo-iN` / `rbrN` |
| subnet, node IPs | as defined | third octet + N |
| DNS domain | as defined | `iN.<domain>` |
| MACs | as defined | third byte = N |
| exposed host ports | as defined | + N × `instance_port_stride` (1000) |

Generated passwords go to the lab's own `.rodeo-secrets.yaml`, so every instance
gets its own; operator secrets stay in `~/.rodeo/secrets.yaml`.
`rodeo instances new <profile> --count N` seeds numbered labs, `rodeo instances
up` deploys them one after another, `rodeo instances list` shows them (plus how
many more fit in this host's free RAM and disk), and `rodeo instances clean`
removes them.

In addon sections, `${node_fqdn:<vm>}` and `${node_ip:<vm>}` stand for that lab's
own node. `lab_in_a_box.dns_aliases: {<vm>: [name, ...]}` adds names a node answers
to inside its instance's network, e.g. the fixed name a pre-built server image was
installed with.

**Workshop tracks:** a top-level `workshop:` block fetches an Instruqt track
into `<lab>/workshop` at deploy time. It then warns about track machines with no
lab VM and about assignment variables with no value:

```yaml
workshop:
  repo: https://github.com/SUSE-Technical-Marketing/instruqt-SMLM
  branch: main
  track: smlms
  skip_vms: [zbastion]                  # machines the lab replaces on purpose
  facts: {SMLM_USERNAME: myadmin}       # [[ Instruqt-Var ]] values, shown on the success screen
```

Not carried over by `rodeo export` (warned at export time): PXE-booted
Harvester nodes (lab-in-a-box has no PXE — use `--skip-unsupported` to export
the rest), exposed-service host port-forwards, storage/image-dir selection, and
exact k3s/rke2 version pins.

---

## What belongs here vs. definition.yaml

| Put it in `rodeo-plan.yaml` | Put it in `definition.yaml` |
|-----------------------------|----------------------------|
| VM resource sizing (RAM, CPU, disk) | Node names, IPs, MACs |
| Credentials and secret references | Network CIDR, gateway, DNS domain |
| Deployment target (baremetal/instruqt/aws) | Start order and etcd join gap |
| Software versions | Exposed services and port mapping |
| Storage device path | Node templates (interface roles) |
| Jinja2 parameters | Host prep requirements (sysctls, SELinux) |

The rule of thumb: the plan is **how big** and **where**. The definition is **what**.
