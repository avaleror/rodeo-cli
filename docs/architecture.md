# rodeo-cli — Architecture & design

Technical reference for contributors and maintainers. For deploying a workshop, see [User guide](user-guide.md).

**Version:** 0.4.0
**License:** Apache-2.0

---

## What rodeo-cli is

`rodeo-cli` deploys the **infrastructure for a rodeo**: a live, hands-on workshop where attendees work against real systems. The default profile (`suse-virt`) builds a nested KVM lab on a single Linux host:

- 3-node **Harvester HCI** cluster (nested VMs)
- **Rancher Prime** on K3s (nested VM)
- Host networking, firewalld DNAT, DNS, and phase orchestration

The tool runs on **cloud instances**, **Instruqt builder VMs**, **local VMs**, or **bare metal** — anywhere you have KVM and enough RAM/disk.

---

## Design goals

| Goal | How it is met |
|------|----------------|
| Declarative lab definition | `rodeo-plan.yaml` + secrets + `-P` / `--paramfile` |
| Plan before apply | `rodeo plan` (read-only diff vs host) |
| Safe resume | Per-plan state, `--from PHASE`, `--force` |
| One orchestrator | `DeployRunner` — no duplicated TUI/plain/bash paths |
| Host setup is idempotent | Ansible roles `kvm_host` + `vms` + `pxe_server` |
| Long waits are cancellable | `threading.Event` + process groups in poll loops |
| Instruqt-safe builds | `deployment_target: instruqt` skips `finalise` until snapshot |
| Self-contained install | Ansible roles bundled in `rodeo/data/ansible/` |
| Minimal first-phase friction | `scripts/bootstrap-sles.sh` (curl | bash) + `rodeo bootstrap` subcommand + `install-deps --link` for global binary + `init --example` for pre-seeded declarative labs. See visual in user-guide. |

**Vision (roadmap):** Terraform-for-labs — declare desired state, preview diff, converge, destroy what you own. See [ROADMAP.md](../ROADMAP.md).

---

## High-level architecture

```
┌─────────────────────────────────────────────────────────────┐
│  CLI (click)                                                │
│  init · plan · deploy · status · clean · ssh · logs · …     │
└──────────────────────────┬──────────────────────────────────┘
                           │ load_config() + validate_config()
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Profile (e.g. SuseVirtProfile)                             │
│  phases · vm_names · guarded_phases · run_phase()           │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  DeployRunner.run() → Iterator[DeployEvent]                 │
│  PhaseStarted · LogLine · ProgressUpdate · PhaseDone · …    │
└───────┬─────────────────────────────┬───────────────────────┘
        │                             │
        ▼                             ▼
┌───────────────────┐       ┌─────────────────────────────┐
│ Ansible phases    │       │ Python phases               │
│ kvm_host, vms,    │       │ cluster · rancher · finalise│
│ pxe_server        │       │                             │
│ ansible-playbook  │       │ ClusterPhase · RancherPhase │
│ -e @vars-file     │       │ LibvirtDriver               │
└───────────────────┘       └─────────────────────────────┘
        │                             │
        ▼                             ▼
┌─────────────────────────────────────────────────────────────┐
│  KVM host (SLES 16 / Leap / Ubuntu / Fedora)                │
│  libvirt · firewalld · virbr0 · 4 nested VMs                │
└─────────────────────────────────────────────────────────────┘
```

**Consumers of the event stream:**

- `rodeo/app.py` — Textual TUI (DeployPanel + LogsPanel)
- `rodeo/commands/deploy.py` — Rich plain output

Neither contains pipeline logic. Both subscribe to the same `DeployRunner` generator.

---

## Why Ansible and Python (not one or the other)

| Layer | Tool | Rationale |
|-------|------|-----------|
| Host packages, systemd, firewalld, sysctl | **Ansible** | Declarative idempotency; SLES 16 edge cases already encoded in roles |
| VM disks, ISOs, XML, cloud-init, network XML | **Ansible** | Template + file generation; community.libvirt modules |
| iPXE boot server (nginx, dnsmasq, boot files) | **Ansible** | `pxe_server` role; two-stage UEFI → TFTP → HTTP |
| VM start order, VIP poll, kubeconfig, nodes Ready | **Python** | 20–90 minute waits, progress UX, cancellation |
| K3s, Rancher API, cluster import | **Python** | Procedural API sequence; structured errors |
| Runtime VM ops (status, clean, restart) | **libvirt-python** | Direct API; avoids virsh where possible |

Bash deploy scripts (`start-vms.sh`, `setup-rancher.sh`, `deploy.sh`) were **retired in v0.3**. Logic lives in `engine/cluster.py` and `engine/rancher.py`.

**Do not replace Ansible roles with Python** without a live KVM regression. The fragile Instruqt boot-order behavior is in the roles.

---

## Repository layout

```
rodeo/
├── cli.py                 Click entry; ConfigError handler
├── config.py              Plan load, Jinja, -P/--paramfile, secrets, validation
├── state.py               ~/.rodeo/state/<plan-name>.yaml (profiles own phase lists; `reset_from` requires phases arg)
├── ssh.py                 Shared SSH opts + helpers (used by engine + commands)
├── app.py                 Textual TUI (event subscriber only)
├── engine/
│   ├── runner.py          DeployRunner, vars file, phase dispatch helpers
│   ├── cluster.py         ClusterPhase
│   ├── rancher.py         RancherPhase
│   └── libvirt.py         LibvirtDriver
├── profiles/
│   ├── base.py            RodeoProfile ABC
│   └── suse_virt.py       Default workshop profile
├── commands/              Thin CLI wrappers
├── widgets/               TUI panels
└── data/
    ├── ansible/           kvm_host + vms + pxe_server roles (bundled)
    ├── deployer/          inventory.local + legacy examples
    └── templates/         init templates

tests/                     pytest (config, runner, cluster, ansible contract, …)
docs/                      architecture.md, user-guide.md, assets/diagrams/
```

---

## Deployment pipeline

Six phases per `SuseVirtProfile`. State is per plan name (`cfg["name"]`).

| Phase | Engine | Summary |
|-------|--------|---------|
| `kvm_host` | Ansible | KVM packages, modular libvirt, NM unmanaged conf, firewalld rules (not started), storage pool, sysctl |
| `vms` | Ansible | Download ISOs/images, virbr0 + DHCP leases, qcow2 disks, config ISOs, define domains (not start); disk-first boot order |
| `pxe_server` | Ansible | nginx on `virbr0:8080`, `ipxe.efi` TFTP, vmlinuz/initrd/rootfs, per-node iPXE scripts + config YAMLs, dnsmasq two-stage boot |
| `cluster` | `ClusterPhase` | firewalld on; virbr0 up; start h1 → VIP → h2 → 90s → h3 → rancher; kubeconfig; 3 nodes Ready |
| `rancher` | `RancherPhase` | K3s, Helm, cert-manager, Rancher Prime, import Harvester, CoreDNS patch, eject ISOs |
| `finalise` | `DeployRunner` | VM autostart + `libvirt-guests` enable |

**Timeouts (nested KVM):** VIP ≤ 60 min, kubeconfig ≤ 30 min, nodes Ready ≤ 90 min, Rancher import ≤ 30 min.

**Instruqt guard:** `finalise` is in `guarded_phases`. Skipped when `deployment_target: instruqt` unless `--finalise`. Running finalise before image save breaks instance boot.

---

## Configuration system

### Precedence (lowest → highest)

1. `_BASE_DEFAULTS` in `config.py`
2. Profile `default_cfg()` (e.g. `SuseVirtProfile`)
3. `rodeo-plan.yaml` (Jinja-rendered if templated)
4. `--paramfile` (deep merge, tfvars-style)
5. `-P dotted.path=value` (CLI overrides)

### Jinja plans

```yaml
parameters:
  memory: 16384
resources:
  harvester:
    memory_mib: {{ memory }}
```

`StrictUndefined` — missing parameters fail at load time, not mid-deploy.

### Secrets (`??` placeholders)

Resolved at load time from `~/.rodeo/secrets.yaml`, `??env:`, `??file:`, or `??cmd:`. `validate_config()` fails closed on unresolved or empty credentials.

Passwords go to Ansible via a **mode-600 vars file** (`-e @file`), never on argv. Password-bearing Ansible template tasks use `no_log: true`.

### Ansible vars file (`DeployRunner._write_vars_file`)

Generated per deploy. Keys Ansible actually consumes:

- `libvirt_flavors` (nested — matches `vm.xml.j2`, `images.yml`)
- `lab_dns_domain` (not `dns_domain`)
- `harvester_version`, `harvester_iso_checksum` (version-keyed map)
- network, passwords, `image_dir`, optional `harvester_token`

### State

`~/.rodeo/state/<plan-name>.yaml` — phase completion, timestamps, `last_error`. chmod 600.

- `rodeo deploy` skips completed phases
- `rodeo deploy --from PHASE` clears that phase and later
- `rodeo deploy --force` ignores state

---

## Event model

```python
DeployRunner.run() yields:
  PhaseStarted(phase)
  PhaseSkipped(phase, reason)   # "done" | "before_start" | "instruqt"
  LogLine(line)
  ProgressUpdate(step, elapsed, total, detail)
  PhaseDone(phase, elapsed)
  PhaseFailed(phase, rc, message)
  DeployComplete()
```

**Failure semantics:**

- Exceptions in `profile.run_phase()` → `PhaseFailed`, pipeline stops
- `runner.stop` checked in all poll loops
- `runner.terminate()` SIGTERMs subprocess group (TUI quit → exit 130)
- Subprocess timeouts in RancherPhase → failed rc, not uncaught exceptions

---

## Workshop topology (suse-virt)

| VM | Role | IP | Default RAM |
|----|------|-----|-------------|
| harvester1 | Bootstrap (alpha) | 192.168.122.11 | 16 GiB |
| harvester2 | Join (bravo) | 192.168.122.12 | 16 GiB |
| harvester3 | Join (charlie) | 192.168.122.13 | 16 GiB |
| rancher | Rancher Prime / K3s | 192.168.122.9 | 8 GiB |

- **VIP:** 192.168.122.10 (Harvester API/UI via kube-vip)
- **DNS domain:** `aerogrid.com` (libvirt dnsmasq + `/etc/hosts`)
- **Host DNAT:** :8443 → Harvester VIP :443, :30002 → Rancher NodePort

![Network and ports (Instruqt → host → nested VMs)](assets/diagrams/rodeo-network-ports.png)

*Editable source:* [`assets/diagrams/rodeo-network-ports.mmd`](assets/diagrams/rodeo-network-ports.mmd)

The diagram shows the full path from a student browser through Instruqt `cloud-client` nginx tabs, host `firewalld` DNAT on **geekohive**, and `virbr0` guests (VIP, Harvester nodes, Rancher, optional LB pool).

VM IPs/user for Python-side ops (ssh, restart, status, cluster waits) come from profile `default_cfg()` + plan `vms` (profile-driven in TUI panels too). Full `vm_nodes` (MACs/UUIDs/flavors + provisioning) still from `roles/vms/defaults/main.yml` (Ansible side). Custom topologies / single inventory source on the roadmap (Phase C). TUI LogsPanel and phase focus now derive VM list from cfg/profile instead of hardcodes.

### Harvester install via iPXE (not ISO-first boot)

Harvester 1.8.0 requires UEFI; legacy BIOS PXE is not supported. The `pxe_server` role provisions network boot on `virbr0`:

```
UEFI firmware (empty disk, no bootloader)
  → DHCP from dnsmasq on 192.168.122.1
  → Stage 1: boot ipxe.efi (TFTP)
  → Stage 2: per-node HTTP script at :8080/ipxe/harvester{1,2,3}
  → kernel + initrd + squashfs over HTTP
  → unattended install (config YAML at :8080/config/config-harvesterN.yaml)
```

VM XML boot order is **disk first, management NIC second**. On first boot the qcow2 is empty, so UEFI falls through to NIC PXE. After install, reboots go straight to disk. ISO CDROMs remain attached as fallback; `RancherPhase` ejects them once the cluster is up.

**On-disk layout after `pxe_server`:**

| Path | Purpose |
|------|---------|
| `/var/lib/libvirt/dnsmasq/ipxe.efi` | Stage-1 UEFI loader (TFTP) |
| `/srv/harvester-pxe/harvester/` | vmlinuz, initrd, rootfs.squashfs, ISO symlink |
| `/srv/harvester-pxe/ipxe/harvester{1,2,3}` | Per-node iPXE scripts |
| `/srv/harvester-pxe/config/config-harvester{N}.yaml` | CREATE/JOIN Harvester config |

Ref: [Harvester v1.8 PXE boot install](https://docs.harvesterhci.io/v1.8/install/pxe-boot-install).

---

## SLES 16 / Instruqt constraints (do not break)

Documented in role comments and [CONTEXT.md](../CONTEXT.md).

1. **NetworkManager only** — wicked removed; virbr0/vnet* marked unmanaged
2. **Modular libvirt** — disable monolithic `libvirtd`; enable socket-activated daemons
3. **libvirt-guests off during build** — stalls `network-online.target` on Instruqt save/reboot
4. **virbr0 autostart false until cluster** — same boot-order issue
5. **firewalld rules permanent, daemon stopped during Ansible** — protects Instruqt mgmt NIC
6. **90s gap between harvester2 and harvester3** — etcd join race prevention
7. **OVMF 4MB non-SecureBoot** — 2MB images gone on SLES 16
8. **xorriso** — not genisoimage

**Files to treat as fragile:**

- `roles/kvm_host/tasks/libvirt.yml`
- `roles/vms/tasks/network_setup.yml`
- `roles/vms/defaults/main.yml` (MACs ↔ DHCP ↔ Harvester config)
- `roles/pxe_server/templates/network-pxe.xml.j2` (two-stage iPXE dnsmasq routing)
- `roles/pxe_server/templates/ipxe-node.j2` (UEFI `initrd=` kernel arg required)
- `engine/cluster.py` (`ETCD_JOIN_GAP`, virbr0 start before VM boot)

---

## Security model (lab scope)

Single-tenant training lab, not production.

| Choice | Reason |
|--------|--------|
| TLS verify off | Self-signed Harvester/Rancher certs |
| SSH `StrictHostKeyChecking=no` | Ephemeral lab VMs, host key baked in cloud-init |
| `security_driver = "none"` | Nested KVM on SELinux-enforcing SLES 16 |
| SELinux permissive (kvm_host role) | Nested virt lab compatibility |
| One host ed25519 key in all guests | Deployer drives SSH/API setup |
| DNAT on host | Attendees reach UIs via host IP |

Protected: secrets in chmod-600 files, `no_log` on password tasks, random passwords/tokens from `rodeo init`, fail-closed validation.

---

## Testing & CI

```bash
ruff check rodeo tests
pytest tests/ -v
```

| Test area | Purpose |
|-----------|---------|
| `test_config.py` | Merge, Jinja, -P, secret resolvers, validation |
| `test_runner.py` | Pipeline events, instruqt guard, vars file |
| `test_ansible_consistency.py` | Profile ↔ Ansible defaults ↔ plan template drift |
| `test_ansible_vars_contract.py` | Vars file keys match Ansible consumers |
| `test_pxe_integration.py` | Playbook order, pxe_server role, boot order, JOIN VIP guard |
| `test_cluster.py` / `test_rancher.py` | Poll loops, timeouts, parsing |
| `test_plan_cmd.py` | Plan diff command |

GitHub Actions: `.github/workflows/ci.yml` — Python 3.10 + 3.12, ruff, pytest.

Live KVM regression is still manual (or geekohive) before touching MAC/DHCP/ISO chain.

---

## Extension points

| Add… | Where |
|------|-------|
| New workshop type | New `RodeoProfile` + register in `profiles/__init__.py` |
| New phase | Add to `profile.phases` + `run_phase()` dispatch |
| New CLI command | `commands/*.py` + register in `cli.py` |
| Host OS support | `install_deps.py` + possibly kvm_host role conditionals |

---

## Related documents

| Document | Audience |
|----------|----------|
| [User guide](user-guide.md) | Workshop operators deploying labs |
| [ROADMAP.md](../ROADMAP.md) | Planned Terraform-for-labs features |
| [CONTEXT.md](../CONTEXT.md) | Full project context for AI/developers |