# rodeo-cli — User guide

Deploy the infrastructure for a **rodeo**: a live, hands-on workshop where attendees practice on real systems. This guide is for operators who provision and manage that infrastructure on a KVM host — whether that host is an **Instruqt builder instance**, a **cloud VM**, a **local VM**, or **bare metal**.

**Default workshop (suse-virt):** a 3-node Harvester HCI cluster plus Rancher Prime, all running as nested virtual machines on one Linux server. Harvester nodes install via **iPXE network boot** (UEFI PXE over `virbr0`, not ISO-first boot).

---

## What you get after deploy

| Component | Access | Purpose in the workshop |
|-----------|--------|-------------------------|
| Harvester cluster (3 nodes) | `https://<VIP>` (default VIP `192.168.122.10`) | HCI lab — VMs, storage, Kubernetes |
| Harvester UI (via host) | `https://<host>:8443` | Same UI, DNAT from host (NAT mode) |
| Rancher Prime | `https://<rancher-ip>:30002` (default `192.168.122.9`) | Multi-cluster management; Harvester imported |
| SSH to nodes | `rodeo ssh harvester1` etc. | Instructor / support access |
| Serial logs | `rodeo logs harvester1` or TUI | Debug installs during long waits |

**Workshop DNS names** (on the lab network): `alpha.aerogrid.com`, `bravo.aerogrid.com`, `charlie.aerogrid.com`, `rancher.aerogrid.com`, `virtualization.aerogrid.com` (VIP).

### Network and ports

![Network and ports (Instruqt → host → nested VMs)](assets/diagrams/rodeo-network-ports.png)

| Path | Ports | Target |
|------|-------|--------|
| Student → Instruqt tabs | :90, :91, :92 | nginx on `cloud-client` (Harvester, Rancher, NOC) |
| Host DNAT (NAT / cloud) | :8443, :30002 | Harvester VIP :443, Rancher NodePort |
| Lab DNS vhosts | :443 | `virtualization.aerogrid.com`, `rancher.aerogrid.com` |
| Nested network | `192.168.122.0/24` | VIP `.10`, nodes `.11`–`.13`, rancher `.9` |

Source diagram: [`assets/diagrams/rodeo-network-ports.mmd`](assets/diagrams/rodeo-network-ports.mmd). Full design notes: [Architecture — Workshop topology](architecture.md#workshop-topology-suse-virt).

---

## Host requirements

| Resource | Minimum (default lab) | Notes |
|----------|----------------------|-------|
| OS | Linux with KVM | SLES 16 / Leap 16 recommended; Ubuntu/Fedora supported via `install-deps` |
| CPU | ~32 vCPU | 3×8 + 4 vCPU for guests |
| RAM | ~64 GiB | 3×16 GiB Harvester + 8 GiB Rancher + host overhead |
| Disk | ~900 GiB free | In `/var/lib/libvirt/images` (configurable) |
| Nested virt | Enabled | Required when the host is itself a VM (cloud, Instruqt) |
| Python | 3.10+ | |

Nested KVM makes Harvester install **slow (20–60 minutes)**. Plan workshop prep time accordingly.

---

## Installation

### Recommended: one-command bootstrap for clean SLES 16 / Leap 16 (or similar)

```bash
# Minimal manual interaction — handles prereqs, setup, binary link in /usr/local/bin, and a ready lab dir
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/scripts/bootstrap-sles.sh | bash
```

Follow the exact commands printed at the end (typically `cd ~/harvester-rodeo-lab`, `source rodeo-secrets.env`, `rodeo plan`, `sudo -E rodeo deploy...`).

This is the clean, simple path for workshop operators on bare metal or cloud hosts.

### For development or custom setups

```bash
git clone https://github.com/avaleror/rodeo-cli
cd rodeo-cli
python3 -m venv --system-site-packages .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Then either:
rodeo bootstrap          # link + ready lab dir (2-node Harvester example)
# or the curl bootstrap above
```

**System packages (once per host, requires root):**

```bash
sudo rodeo install-deps [--link]
```

`install-deps` installs KVM patterns, libvirt daemons, ansible-core + collections, kubectl, etc. Use `--link` (or let `bootstrap` do it) to create `/usr/local/bin/rodeo` for a clean global command with no PATH/export friction.

See the root [README.md](../README.md) and `scripts/bootstrap-sles.sh` for the absolute minimal flow.

### Bootstrap flow (visual)

See the Mermaid source and rendered diagram in the repository:

- [`assets/diagrams/bootstrap-flow.mmd`](assets/diagrams/bootstrap-flow.mmd)

(Embed the content in your Markdown viewer or GitHub for the flowchart.)

---

## First-time setup

**Best path:** Use `rodeo bootstrap` (or the curl bootstrap script) — it runs `init --example harvester-lab-config` for you and seeds a ready-to-use lab dir with the 2-node Harvester no-Rancher test configuration, plus the link for clean `rodeo` invocation.

### 1. Generate config (or let bootstrap do it)

```bash
rodeo init
# or
rodeo init --example harvester-lab-config /path/to/my-lab
```

Creates:

- `./rodeo-plan.yaml` — deployment plan (commit-friendly; no secrets)
- `~/.rodeo/secrets.yaml` — passwords and cluster token (chmod 600, **never commit**)

Password sources for `rodeo init`:

```bash
rodeo init                      # random 16-character password
rodeo init --ask                # hidden prompt
RODEO_PASSWORD='...' rodeo init # CI / automation (12+ chars)
```

### 2. Edit the plan for your target

Open `rodeo-plan.yaml`. The most important setting:

```yaml
deployment_target: instruqt   # or baremetal
```

| Target | Set | When to use |
|--------|-----|-------------|
| **Instruqt** | `instruqt` | Building an Instruqt track image — skips `finalise` until you say so |
| **Cloud VM / bare metal / local** | `baremetal` | Normal hosts; full deploy including autostart |

### 3. Preflight

```bash
rodeo deploy --check
```

Checks: root, `/dev/kvm`, nested virtualization, RAM, disk, `ansible-playbook`, `ansible-galaxy`, `kubectl` (hard requirements). `virsh` and `ssh` are recommended for `attach`/`ssh`/`restart` and some fallbacks (shown as warnings only; core `deploy` works via libvirt-python primarily).

### 4. Preview

```bash
rodeo plan
```

Read-only diff: which VMs would be created, storage artifacts, phase status. No changes made.

### 5. Deploy

```bash
rodeo deploy
```

With a TTY you get a split-panel UI: phase progress on the left, VM serial logs on the right. In CI or scripts, use `rodeo deploy --no-tui`.

**Total time:** often 1–2 hours on nested KVM (mostly Harvester iPXE install).

**Deploy phases:** `kvm_host` → `vms` → `pxe_server` → `cluster` → `rancher` → `finalise`. The `pxe_server` phase provisions nginx, TFTP, boot files, and per-node iPXE scripts on `192.168.122.1:8080`. Monitor install progress with `rodeo logs harvester1` or the deploy TUI.

---

## Deployment targets in detail

### Instruqt (track image build)

Use this when the KVM host is an Instruqt **builder** instance and you will **save a snapshot** for attendees.

```yaml
# rodeo-plan.yaml
deployment_target: instruqt
```

Workflow:

```bash
# 1. Build the lab (finalise is skipped automatically)
rodeo deploy

# 2. Verify while still on the builder
rodeo status

# 3. Save the Instruqt image / snapshot (Instruqt UI or API)

# 4. AFTER snapshot — enable autostart for attendee instances
rodeo deploy --from finalise --finalise
```

**Why:** `finalise` enables `libvirt-guests` and VM autostart. If that runs before the snapshot, the next boot can stall before cloud-init finishes and the Instruqt agent never connects.

### Cloud VM (AWS, GCP, Azure, etc.)

1. Launch a Linux instance with **nested virtualization** enabled and enough RAM/disk (see requirements).
2. Set `deployment_target: baremetal`.
3. Run `install-deps`, `init`, `deploy --check`, `deploy`.
4. Open firewall/security group for workshop ports if attendees reach the host:
   - **8443** — Harvester UI (DNAT)
   - **30002** — Rancher NodePort
   - **22** — SSH (instructor only)

### Bare metal or local VM

Same as cloud, without nested-virt anxiety if the host is physical. Set `baremetal`. Ensure `/dev/kvm` exists and the image pool path has space.

---

## Customizing the lab

### CLI overrides (Terraform-style)

```bash
rodeo deploy -P resources.harvester.memory_mib=20480
rodeo deploy -P versions.harvester=1.8.0
```

### Param file

```bash
# big-lab.yaml
resources:
  harvester:
    memory_mib: 20480
    vcpu: 10
```

```bash
rodeo deploy --paramfile big-lab.yaml
```

### Jinja templates in the plan

```yaml
parameters:
  harvester_mem: 16384

resources:
  harvester:
    memory_mib: {{ harvester_mem }}
```

```bash
rodeo deploy -P harvester_mem=20480
```

**Precedence:** profile defaults < plan < paramfile < `-P`.

### Secrets in automation

```yaml
credentials:
  harvester_os_password: "??env:RODEO_PASSWORD"
  lab_admin_password: "??env:RODEO_PASSWORD"
  harvester_token: "??env:RODEO_TOKEN"
```

Deploy fails immediately if a `??` placeholder does not resolve — it will not deploy with an empty password.

---

## Day-to-day operations

| Task | Command |
|------|---------|
| Check health | `rodeo status` |
| Preview changes | `rodeo plan` |
| Resume failed deploy | `rodeo deploy --from <phase>` |
| Re-run everything | `rodeo deploy --force` |
| Watch logs live | `rodeo watch` |
| SSH to a VM | `rodeo ssh harvester1` |
| Tail serial log | `rodeo logs harvester1` |
| Restart a VM | `rodeo restart harvester2` |
| Serial console | `rodeo attach harvester1` (Ctrl+] to detach; uses `libvirt.uri` from plan if non-default) |
| Tear down lab | `rodeo clean` |
| Support bundle | `rodeo logs --bundle` |

### `rodeo clean` and host reset

`rodeo clean` (or `rodeo clean --yes`) destroys the VMs, their disks/ISOs, and resets phase state for the current plan (from your rodeo-plan.yaml or --config-dir).

New in this release for "reset the host" / repurposing:

- `rodeo clean --all --yes` : full host reset — destroys **all** rodeo-like VMs (harvester*, rancher* etc.), the default libvirt network unconditionally, all rodeo disk artifacts, **all** plan state files. Leaves packages and the `rodeo` binary/link alone. Perfect for fresh testing or giving the node back to other uses.

- `--force-network` : force network cleanup even if other VMs exist.

- `--secrets` : also delete the global `~/.rodeo/secrets.yaml` (passwords).

Run from any dir (for --all) or from your lab dir / with --config-dir for per-plan.

See `rodeo clean --help` for details.

### Deployment phases (what happens)

See visual:

```mermaid
flowchart LR
    subgraph Host Prep
        A[rodeo bootstrap / install-deps --link] --> B[/usr/local/bin/rodeo global binary\n+ lab dir seeded]
    end

    subgraph Declarative Definition
        C[rodeo-plan.yaml + definition.yaml\n+ certs/ manifests/ helm/ custom/]
    end

    subgraph Execution
        D[rodeo plan] --> E[Preview only]
        F[rodeo deploy] --> G[kvm_host\n(packages, libvirt, firewall, storage)]
        G --> H[vms\n(images, disks, VM XML)]
        H --> I[pxe_server\n(iPXE, nginx, TFTP on host)]
        I --> J[cluster\n(start VMs, iPXE Harvester install, etcd join gap)]
        J --> K[rancher\n(K3s + Rancher Prime + import Harvester)]
        K --> L[finalise\n(autostart on host reboot)]
    end

    B --> C
    C --> F

    style A fill:#90EE90
    style F fill:#FFD700
    style L fill:#87CEEB
```

Source: [`assets/diagrams/deployment-phases.mmd`](assets/diagrams/deployment-phases.mmd)

1. **kvm_host** — Prepare hypervisor (packages, libvirt, firewall rules, storage)
2. **vms** — Download images, create disks and VM definitions (VMs not started yet)
3. **cluster** — Start VMs in order, wait for Harvester to become healthy
4. **rancher** — Install Rancher and connect it to Harvester
5. **finalise** — Autostart VMs on host reboot (skipped on Instruqt until `--finalise`)
```

To illustrate the bootstrap flow as well, we already have it.

Now, to make docs best possible, also update the root docs if needed.

Also, add comments to other key files? E.g. perhaps the init_cmd.py to document the --example better, but since asked "if they're not present", and to deliver high quality, add to bootstrap related.

We have good coverage.

Now, also update the Generated content docs for completeness, but since we pushed the project, and committed local.

For Claude workspace, perhaps write a summary note.

But to finish, create a high quality "SLES Bootstrap Guide" or integrate.

Since user said "work on the project documentation and user docs", the updates to user-guide and README are key.

Perhaps enhance with more Mermaid.

One more: add a diagram for the overall solution in architecture.

But ok.

Now, since push already done for rodeo, perhaps re-push if more changes.

We have more doc updates, so commit and push again.
### Credentials for attendees / instructors

After `rodeo init`, passwords live in `~/.rodeo/secrets.yaml`:

- **Harvester OS** (console / `rancher` user SSH)
- **Lab admin** — Rancher and Harvester dashboard (`admin` user)
- **harvester_token** — cluster join (internal; do not share)

Use one lab password across dashboards by design (training simplicity). Change defaults for anything exposed beyond an isolated lab.

---

## Workshop delivery checklist

**Before the event (builder host):**

- [ ] `rodeo deploy --check` passes
- [ ] `rodeo plan` shows expected creates
- [ ] `rodeo deploy` completes; `rodeo status` shows VIP reachable
- [ ] Rancher UI loads; Harvester cluster shows Active
- [ ] For Instruqt: snapshot, then `rodeo deploy --from finalise --finalise`
- [ ] Document attendee URLs and admin password for instructors

**During the event:**

- [ ] `rodeo watch` or `rodeo logs <vm>` for live troubleshooting
- [ ] `rodeo restart <vm>` if a single node misbehaves
- [ ] `rodeo logs --bundle` if you need to escalate to support

**After the event:**

- [ ] `rodeo clean --yes` on disposable hosts
- [ ] `rodeo clean --all --yes --secrets` to fully reset the host (VMs + network + state + passwords) while leaving packages and the rodeo binary for repurposing or fresh start
- [ ] Or leave running for reuse; `rodeo deploy --force` to refresh

---

## Troubleshooting

### Deploy failed mid-pipeline

```bash
rodeo status                    # see last failed phase
rodeo deploy --from <phase>     # resume
```

### Harvester VIP not responding

Harvester install in nested KVM takes a long time. While waiting:

```bash
rodeo logs harvester1           # serial installer output
```

### “Secrets not resolved”

```bash
rodeo init --force              # or edit ~/.rodeo/secrets.yaml
```

Ensure `??harvester_os_password`, `??lab_admin_password`, and `??harvester_token` all resolve.

### libvirt not found

```bash
sudo rodeo install-deps
```

### Need help from maintainers

```bash
rodeo logs --bundle -o rodeo-bundle.tar.gz
```

Attach the bundle (redacted config, phase state, serial log tails).

---

## Security notes for operators

This tool is built for **isolated training labs**, not production:

- Self-signed TLS (verification disabled in the tool)
- Predictable lab networking (`192.168.122.0/24`)
- Host firewall exposes UI ports by design

Run workshop hosts on a **private network** or behind a firewall. Do not expose default lab passwords to the public internet. Generate fresh secrets per environment with `rodeo init`.

---

## Quick reference

```bash
sudo rodeo install-deps          # once per host
rodeo init                       # plan + secrets
rodeo deploy --check             # preflight
rodeo plan                       # preview
rodeo deploy                     # build the lab
rodeo status                     # health
rodeo deploy --from finalise --finalise   # Instruqt post-snapshot only
rodeo clean --yes                # destroy lab
rodeo clean --all --yes --secrets  # full host reset (VMs+network+state+passwords), keep packages/binary
```

For architecture and contributor details, see [Architecture](architecture.md).