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
to **1200** when lower, sets `storage.backend: nvme`, and mounts the largest non-root
NVMe on `image_dir`. Prefer **`i7i.8xlarge`**. Nested virt is enabled by default on
non-metal types.

### `provider` (when `deployment_target: aws`)

Same shape as Fleet [`workshop.yaml` provider](../fleet.md#workshopyaml-provider-schema).
Required fields for AWS: `type`, `region`, `subnet_id`, `security_group_ids`, and
either **`instance_type`** or **`instance_tier`**.

**Instance size (single-host v1):** pick one of three tiers for the lab profile, or set
an explicit type. `rodeo up --target aws` prompts interactively when neither is set;
with `--yes` it uses **recommended**.

| Tier | Meaning |
|------|---------|
| `budget` | Meets the profile minimum (often EBS-backed Nitro) |
| `recommended` | Preferred size (usually `i7i.*` local NVMe for Harvester/Edge) |
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
  # ami omitted → openSUSE Leap 16.0 (x86_64)* from aws-marketplace
  # ami: ami-…                        # optional pin (SLES 16 / Leap)
  # ami_name_filter: "openSUSE Leap 16.0 (x86_64)*"
  subnet_id: subnet-…
  security_group_ids: [sg-…]          # 22, 8443, 30002
  ssh_user: ec2-user                  # Leap Marketplace default; sles for some SLES AMIs
  # nested_virtualization: true       # default on for non-metal
  # volume_size_gib: 100              # root EBS; lab disks use NVMe
```

```bash
rodeo up --yes --profile harvester --target aws --instance-tier recommended
```

**AWS API credentials** (boto3 — never in the plan): `~/.aws/credentials` /
`AWS_PROFILE`, **or** `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`
(+ optional `AWS_SESSION_TOKEN`). AWS CLI is optional.

**Marketplace:** subscribe once to
[openSUSE Leap](https://aws.amazon.com/marketplace/pp/prodview-wn2xje27ui45o)
(or your SLES AMI) in the target account/region before first provision.

**SSH / root:** new instances get cloud-init UserData that installs the managed
pubkey for **root** and **passwordless sudo** for `ssh_user` (`ec2-user` on Leap).
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

---

## lab_in_a_box — exporting to lab-in-a-box

`rodeo export --format lab-in-a-box` renders the lab as the `lab.json` that
[lab-in-a-box](https://github.com/SUSE-Technical-Marketing/lab-in-a-box)'s
`setup_lab.sh` / `destroy_lab.sh` consume (tested against release 1.0.0). The
plan and definition stay the source of truth; the optional `lab_in_a_box:`
block holds the knobs that only exist on the lab-in-a-box side:

```yaml
lab_in_a_box:
  iso_image: openSUSE-Leap-15.6.qcow2   # base qcow2 in lab-in-a-box's ISO_LOC (required to deploy)
  config_method: cloud-init             # cloud-init (default) | iso-cloud-init | "" (ignition/combustion)
  cluster_name: mgmt                    # kcluster name (also its DNS record: <name>.<domain>)
  cluster_type: k3s                     # k3s (default) | rke2
  clu_rel: stable                       # install channel — exact version pins don't carry over
  addons: [rancher]                     # override the derived install_<addon> list
  sections:                             # verbatim extra/override lab.json sections
    rancher: {rancher_rel: stable}
```

Not carried over (warned at export time): PXE-booted Harvester nodes
(lab-in-a-box has no PXE — use `--skip-unsupported` to export the rest),
exposed-service host port-forwards, storage/image-dir selection, and exact
k3s/rke2 version pins.

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
