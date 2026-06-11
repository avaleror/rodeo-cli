# rodeo-cli — Project Context

This document gives any developer or AI assistant enough context to audit, extend, or take over this project without needing the original conversation history.

---

## What this is

`rodeo-cli` is a Python CLI tool that deploys and manages the **SUSE Virtualization Rodeo** — an Instruqt-based training lab that runs a 3-node Harvester HCI cluster plus Rancher Prime, all inside nested KVM virtual machines on a single SLES 16 host.

It replaces `rodeo.sh`, a monolithic bash script in the parent repository. The goals for the rewrite were:

- Structured phase tracking with clean resume support (`--from PHASE`)
- Direct libvirt-python VM operations instead of shelling out to virsh
- A Textual split-panel TUI showing deploy progress and VM serial logs side by side
- Self-contained packaging: Ansible roles and deployer scripts ship inside the Python package
- Infra-agnostic: works on cloud VMs, bare metal, or local machines — not just inside Instruqt

**GitHub:** https://github.com/avaleror/rodeo-cli  
**Author:** Andres Valero, Principal Technology Advocate at SUSE  
**Version:** 0.2.0  
**Python:** 3.10+  

---

## Relationship to instruqt-virtualization

`rodeo-cli` is a sibling project to `avaleror/instruqt-virtualization` (private). That repo contains:
- The Instruqt challenge definitions (tabs, setup scripts, solve scripts)
- The same Ansible roles that are now **bundled** in `rodeo-cli/rodeo/data/ansible/`
- The same deployer scripts now bundled in `rodeo-cli/rodeo/data/deployer/`
- `rodeo.sh` — the original bash deploy script that `rodeo-cli` replaces

The Ansible roles in `rodeo/data/ansible/` are a **snapshot copy** from `instruqt-virtualization`. If that repo's roles change, the bundle here needs a manual sync. Long-term plan: make `rodeo-cli` the source of truth and have `instruqt-virtualization` reference it.

---

## Infrastructure overview

What the deployment creates on the KVM host:

| VM | Role | IP | MAC (mgmt) | Memory |
|---|---|---|---|---|
| harvester1 | Harvester bootstrap node (alpha) | 192.168.122.11 | 02:00:00:0D:62:E1 | 16 GiB |
| harvester2 | Harvester join node (bravo) | 192.168.122.12 | 02:00:00:0D:62:E2 | 16 GiB |
| harvester3 | Harvester join node (charlie) | 192.168.122.13 | 02:00:00:0D:62:E3 | 16 GiB |
| rancher | Rancher Prime on K3s (root) | 192.168.122.9 | 02:00:00:0D:62:E9 | 8 GiB |

- **VIP:** `192.168.122.10` — kube-vip floating IP for the Harvester cluster API/UI
- **Network:** libvirt NAT (virbr0), dnsmasq on 192.168.122.1, DNS domain `aerogrid.com`
- **Disk:** 270 GiB per Harvester node (Longhorn storage), 60 GiB for Rancher
- **UEFI:** OVMF 4MB non-SecureBoot (`/usr/share/qemu/ovmf-x86_64-4m-{code,vars}.bin`)
- **Harvester NICs per node:** eth0 management, eth1 storage, eth2 migration, eth3/eth4 service (Kube-OVN)

Host sizing for the default config: ~64 GB RAM, ~32 vCPU, ~900 GB free disk in `/var/lib/libvirt/images`.

---

## Architecture

```
rodeo/
├── cli.py                  Click group — registers all commands
├── config.py               Load rodeo-plan.yaml + ~/.rodeo/secrets.yaml
├── state.py                Phase state YAML at ~/.rodeo/state.yaml
├── app.py                  Textual TUI App + all Message classes + workers
├── widgets/
│   ├── deploy_panel.py     Left panel: DataTable phases + RichLog ansible stream
│   └── logs_panel.py       Right panel: TabbedContent VM serial logs
├── engine/
│   └── libvirt.py          LibvirtDriver — direct libvirt-python VM/network ops
├── commands/
│   ├── deploy.py           Pipeline orchestrator — TUI mode or plain Rich
│   ├── install_deps.py     zypper/apt/dnf packages + ansible-core + collections
│   ├── init_cmd.py         Scaffold rodeo-plan.yaml + ~/.rodeo/secrets.yaml
│   ├── status.py           One-shot VM table + VIP reachability + phase progress
│   ├── clean.py            Destroy VMs, disks, ISOs, network, reset state
│   ├── restart.py          ACPI shutdown + start for one VM or all
│   ├── ssh_cmd.py          exec ssh into a VM (known IPs + users)
│   ├── logs.py             exec tail -f on VM serial log
│   ├── watch.py            Open TUI in view-only mode (no deploy)
│   └── attach.py           exec virsh console <vm>
└── data/
    ├── ansible/            Bundled Ansible roles (snapshot from instruqt-virtualization)
    │   ├── playbook.yml    Entry point: roles kvm_host + vms with tags
    │   ├── requirements.yml community.general, community.libvirt, ansible.posix
    │   ├── group_vars/all.yml VIP, rancher_ip, network_mode defaults
    │   └── roles/
    │       ├── kvm_host/   Packages, libvirt modular daemons, firewalld, storage
    │       └── vms/        Network XML, VM XML, cloud-init ISOs, Harvester config ISOs
    ├── deployer/           Bundled deployer scripts
    │   ├── inventory.local [kvm_host] localhost ansible_connection=local
    │   └── lib/
    │       ├── start-vms.sh  Start VMs sequentially, wait for VIP + kubeconfig + 3 nodes Ready
    │       └── setup-rancher.sh  K3s + cert-manager + Rancher Prime + Harvester import
    └── templates/
        ├── rodeo-plan.yaml  Default plan template (copied by rodeo init)
        └── secrets.yaml     Secrets template (copied by rodeo init)
```

---

## Deployment pipeline

Five phases, tracked in `~/.rodeo/state.yaml`. Each phase is idempotent — re-running is safe. Use `rodeo deploy --from PHASE` to resume after a failure.

### Phase 1: kvm_host

Runs: `ansible-playbook -i deployer/inventory.local ansible/playbook.yml --tags kvm_host`

What it does (via the `kvm_host` Ansible role):
- Installs zypper patterns `kvm_server` + `kvm_tools` and individual packages (xorriso, qemu-tools, OVMF, firewalld, python3-libvirt-python, kubectl)
- Enables modular libvirt daemon sockets (virtqemud, virtnetworkd, etc.) — SLES 16 uses modular, not the monolithic `libvirtd`
- Disables monolithic `libvirtd.service` + `libvirtd.socket` (package post-install scripts often re-enable them — this causes boot failures on Instruqt)
- Disables `libvirt-guests` service during build phase (re-enabled in finalise)
- Writes `/etc/sysconfig/libvirt-guests` (ON_SHUTDOWN=shutdown, PARALLEL_SHUTDOWN=4, etc.)
- Marks virbr0 and vnet* as unmanaged in NetworkManager (`/etc/NetworkManager/conf.d/99-libvirt-unmanaged.conf`) — SLES 16 uses NM, not wicked
- Configures firewalld DNAT rules (port 8443 → Harvester VIP:443, port 30002 → Rancher nodeport)
- Creates libvirt storage pool at `/var/lib/libvirt/images`
- Writes `/etc/hosts` entries for aerogrid.com names

### Phase 2: vms

Runs: `ansible-playbook -i deployer/inventory.local ansible/playbook.yml --tags vms`

What it does (via the `vms` Ansible role):
- Downloads Harvester ISO and Leap 16 qcow2 (for the Rancher VM) to the image pool
- Defines + activates the libvirt default NAT network (virbr0) with static DHCP leases per VM MAC
- Creates qcow2 disks for all 4 VMs + OVMF vars copies per Harvester node
- Generates Harvester config ISOs (node1 as bootstrap, node2/3 as join) with VIP, token, SSH keys
- Generates cloud-init ISO for the Rancher VM (sets root password, injects SSH key)
- Defines all 4 libvirt domains via XML (does NOT start them — that's phase cluster)
- Writes DNS entries to /etc/hosts for aerogrid.com names
- Note: virbr0 autostart is intentionally set to `false` at this stage (set true in finalise)

### Phase 3: cluster

Runs: `deployer/lib/start-vms.sh` (bash script, not Ansible) after starting firewalld.

What it does:
1. `systemctl start firewalld && firewall-cmd --reload` — Ansible only writes permanent rules, does not start firewalld (to avoid disrupting cloud-init on Instruqt instances)
2. `virsh start harvester1` — bootstrap node first
3. Polls `https://VIP` every 30s up to 3600s (Harvester install takes 20-60 min in nested KVM)
4. Once VIP responds: `virsh start harvester2`, sleep 90s, `virsh start harvester3`, `virsh start rancher`
5. SSH into VIP as `rancher` user to fetch `/etc/rancher/rke2/rke2.yaml` → rewrite 127.0.0.1 to VIP
6. Polls `kubectl get nodes` (via the fetched kubeconfig) until 3 nodes show Ready (up to 90 min)

### Phase 4: rancher

Runs: `deployer/lib/setup-rancher.sh` (bash script) via SSH into the Rancher VM.

What it does:
1. Waits for SSH on 192.168.122.9
2. Installs K3s with `--disable traefik` and `--write-kubeconfig-mode 644`
3. Waits for K3s node Ready
4. Installs Helm + cert-manager (via Helm chart)
5. Installs Rancher Prime (via Helm chart, NodePort 30002)
6. Waits for Rancher to be reachable
7. Sets the Rancher admin password
8. Imports the Harvester cluster into Rancher (via Rancher API + kubectl apply of the agent manifest on the Harvester VIP)

Environment variables the script expects (all set by `rodeo deploy` from config):
- `RANCHER_VM_IP`, `RANCHER_VERSION`, `K3S_VERSION`, `HARVESTER_VIP`
- `HARVESTER_OS_PASSWORD` (also used as Rancher admin password)
- `LAB_ADMIN_PASSWORD`, `CERT_MANAGER_VERSION`

### Phase 5: finalise

Runs entirely via Python (no Ansible, no shell scripts).

What it does:
- Sets `virsh autostart` on all 4 VMs via `LibvirtDriver.set_autostart(vm, True)`
- `systemctl enable libvirt-guests` — VMs will gracefully shutdown on host stop and restart on host boot

This is the only phase that should NOT be run during an Instruqt build (before image save). Running it before save means libvirt-guests will try to start VMs on the next boot before cloud-init finishes, blocking network-online.target and preventing the Instruqt agent from connecting.

---

## Configuration system

### rodeo-plan.yaml (in working directory)

The deployment plan. Generated by `rodeo init`. All values have sensible defaults. Example:

```yaml
name: suse-virt-rodeo

network:
  mode: nat           # nat | bridge
  vip: "192.168.122.10"
  rancher_ip: "192.168.122.9"
  gateway: "192.168.122.1"
  dns_domain: "aerogrid.com"

resources:
  harvester:
    memory_mib: 16384
    vcpu: 8
    disk_gb: 270
  rancher:
    memory_mib: 8192
    vcpu: 4
    disk_gb: 60

versions:
  harvester: "1.8.0"
  rancher: "2.13.1"
  k3s: "v1.31.4+k3s1"
  cert_manager: "v1.16.2"

credentials:
  harvester_os_password: "??harvester_os_password"   # resolved from secrets
  lab_admin_password: "??lab_admin_password"

ansible:
  path: null           # null = use bundled data in rodeo/data/
  inventory: "deployer/inventory.local"

libvirt:
  uri: "qemu:///system"
```

### ~/.rodeo/secrets.yaml (chmod 600, never committed)

```yaml
harvester_os_password: "Foobar12345$"
lab_admin_password: "Foobar12345$"
```

### Secret resolution

`config.py:_resolve_secrets()` walks the loaded plan dict and replaces any string starting with `??key` with `secrets[key]`. This happens before the config is returned to any command.

### Ansible path resolution (`config.py:find_ansible_root()`)

Returns the directory containing `ansible/playbook.yml`. Search order:
1. `cfg['ansible']['path']` (from plan)
2. `RODEO_ANSIBLE_PATH` environment variable
3. `rodeo/data/` (bundled — default for installed tool)
4. Current working directory
5. `~/instruqt-virtualization` (for developers with the parent repo checked out)

### Extra vars passed to Ansible

`deploy.py:_build_extra_vars()` passes these to every `ansible-playbook` invocation:
- `network_mode`, `host_bridge`, `harvester_vip`, `rancher_ip`
- `harvester_os_password`, `rancher_vm_password`

These override group_vars, which override role defaults — standard Ansible precedence.

---

## State management

`state.py` reads and writes `~/.rodeo/state.yaml`. Structure:

```yaml
phases:
  kvm_host:
    completed: true
    timestamp: "2024-01-15T12:00:00+00:00"
  vms:
    completed: true
    timestamp: "2024-01-15T12:04:22+00:00"
  cluster:
    completed: false
    last_error: "start-vms.sh exited 1"
    timestamp: "2024-01-15T12:35:10+00:00"
```

Key functions:
- `mark_phase_done(phase)` — write completed=True + timestamp
- `mark_phase_failed(phase, error)` — write completed=False + last_error
- `reset_from(phase)` — delete phase and all subsequent phases from state
- `is_phase_done(phase)` — boolean check (used to skip already-complete phases)

`rodeo deploy --from vms` calls `reset_from("vms")` before starting, clearing vms/cluster/rancher/finalise from state so they re-run.

---

## Textual TUI

### Layout

```
┌─ rodeo  12:34:56 ──────────────────────────────────────────────────┐
│ Deploy                          │ VM Serial Logs                    │
│                                 │  harvester1 │ h2 │ h3 │ rancher  │
│  Phase       Status    Elapsed  │ ─────────────────────────────── │
│  kvm_host    ✓ done    3:44     │ [12:00:01] Starting installer... │
│  vms         ✓ done    8:12     │ [12:00:02] Loading kernel...     │
│  cluster     ▶ running          │ [12:00:03] Waiting for network   │
│  rancher     ○ pending          │ ...                              │
│  finalise    ○ pending          │                                  │
│                                 │                                  │
│ ▶ cluster                       │                                  │
│ [start-vms] Started harvester1  │                                  │
│ [start-vms] 30s / 3600s...      │                                  │
└─────────────────────────────────┴──────────────────────────────────┘
 q Quit
```

### Component structure

`RodeoApp` (app.py) orchestrates everything:
- `DeployPanel` (widgets/deploy_panel.py) — left 40%
  - `DataTable` `#phases-table`: phase | status | elapsed columns; rows keyed by phase name
  - `Label` `#phase-sep`: current active phase banner
  - `RichLog` `#ansible-log`: Ansible/script stdout, auto-scrolling
- `LogsPanel` (widgets/logs_panel.py) — right 60%
  - `TabbedContent` with one `TabPane` + `RichLog` per VM

### Message-passing pattern (thread safety)

All background work runs in `@work(thread=True)` thread workers. Workers cannot call Textual widget methods directly (those must run in the asyncio event loop). They post `Message` objects instead via `self.post_message()`, which is thread-safe.

Message classes defined in `app.py`:
- `_PhaseStarted(phase)` → `on__phase_started` → `DeployPanel.set_phase_running()`
- `_PhaseSkipped(phase)` → `on__phase_skipped` → `DeployPanel.set_phase_skipped()`
- `_PhaseDone(phase, elapsed)` → `on__phase_done` → `DeployPanel.set_phase_done()`
- `_PhaseFailed(phase, rc)` → `on__phase_failed` → `DeployPanel.set_phase_failed()`
- `_AnsibleLine(line)` → `on__ansible_line` → `DeployPanel.append_ansible()`
- `_LogLine(vm, line)` → `on__log_line` → `LogsPanel.append_log()`
- `_DeployComplete()` → `on__deploy_complete` → updates subtitle + banner
- `_DeployFailed(phase)` → `on__deploy_failed` → updates subtitle

### Workers

`_run_deploy()` — single thread worker, runs all phases sequentially. Posts phase messages before/after each. Stores the current `subprocess.Popen` handle in `self._ansible_proc` so `action_quit()` can terminate it cleanly.

`_tail_vm(vm)` — one thread worker per VM (spawned by `_start_log_tailers()`). Opens the serial log file, seeks to end, then reads new lines in a 0.3s poll loop. Serial logs are at `/var/log/libvirt/qemu/<vm>_serial.log` — only created once the VM first boots.

### TUI launch logic (deploy.py)

```python
use_tui = sys.stdout.isatty() if tui is None else tui
if use_tui:
    app = RodeoApp(cfg, root, from_phase, install_collections)
    app.run()
else:
    _deploy_plain(cfg, root, from_phase, install_collections)
```

In non-TTY environments (CI, scripts, pipe), the TUI is skipped automatically and Rich progress output is used instead.

---

## LibvirtDriver (engine/libvirt.py)

Thin wrapper around `python-libvirt`. Handles `ImportError` gracefully — if libvirt-python is not installed, any operation raises `RuntimeError` with a message pointing to `rodeo install-deps`.

Key methods:
- `list_vms(names)` → `list[VMInfo]` — state + autostart for each VM
- `start(name)` / `shutdown(name)` / `destroy(name)` — ACPI vs force
- `undefine(name)` — with `VIR_DOMAIN_UNDEFINE_NVRAM` flag (needed for UEFI VMs)
- `set_autostart(name, bool)` — used by finalise phase
- `net_start/net_destroy/net_undefine` — manage the `default` libvirt network
- `storage_vol_delete(pool, vol)` — delete a disk image via the libvirt API

Used as a context manager:
```python
with LibvirtDriver("qemu:///system") as lv:
    vms = lv.list_vms()
```

---

## Key SLES 16 constraints

These are the non-obvious things that burned time during development:

1. **No wicked — NetworkManager only.** SLES 16 dropped wicked. All network config goes through `nmcli`/`NetworkManager`. The Ansible role uses `/etc/NetworkManager/conf.d/99-libvirt-unmanaged.conf` to stop NM from managing virbr0 and vnet* interfaces.

2. **Modular libvirt daemons, not monolithic libvirtd.** SLES 16 uses socket-activated `virtqemud`, `virtnetworkd`, etc. The monolithic `libvirtd.service` is gone. The kvm_server zypper pattern often enables the old `libvirtd.service` via post-install scripts — the Ansible role explicitly disables it.

3. **Boot failure root cause.** On Instruqt instances, libvirtd or libvirt-guests starting at boot activates virbr0/dnsmasq before cloud-init finishes. This stalls `network-online.target` and prevents the Instruqt agent from connecting after an image save/reboot. Fix: disable both services during build; only re-enable libvirt-guests in phase finalise (which runs after the image is saved, when the lab is already live).

4. **OVMF path.** The 2MB OVMF images are gone on SLES 16. Use the 4MB non-SecureBoot variants: `/usr/share/qemu/ovmf-x86_64-4m-code.bin` and `ovmf-x86_64-4m-vars.bin` from the `qemu-ovmf-x86_64` package.

5. **xorriso not genisoimage.** `genisoimage` is removed on SLES 16. The `vms` role uses `xorriso` to build the Harvester config ISOs and cloud-init seed ISOs.

6. **Nested KVM is slow.** Harvester install takes 20-60 minutes in nested KVM vs 15-20 on bare metal. The VIP wait timeout in `start-vms.sh` is 3600s (1 hour).

7. **kubectl install.** kubectl is not in the SUSE base repos. The `kvm_host` role adds the upstream Kubernetes zypper repo (`pkgs.k8s.io/core:/stable:/v1.36/rpm/`).

---

## Commands reference

| Command | File | Notes |
|---|---|---|
| `install-deps` | commands/install_deps.py | Requires root. Detects zypper/apt/dnf. Installs ansible-core + collections. |
| `init [DIR]` | commands/init_cmd.py | Copies templates to DIR/rodeo-plan.yaml and ~/.rodeo/secrets.yaml (chmod 600). |
| `deploy` | commands/deploy.py | Auto-launches TUI if TTY. `--from PHASE` to resume. `--no-tui` for plain output. |
| `clean` | commands/clean.py | Confirms before destroying. Calls virsh destroy+undefine, deletes disk globs, resets state from kvm_host. |
| `status` | commands/status.py | Connects libvirt, builds VM table, probes VIP via HTTPS, shows phase state. |
| `watch` | commands/watch.py | Opens TUI in watch_only=True mode (no deploy worker). |
| `restart VM\|all` | commands/restart.py | ACPI shutdown then start. `--hard` for force-kill. Waits 90s for clean stop. |
| `ssh VM` | commands/ssh_cmd.py | `os.execvp("ssh", ...)` with known IP/user/key. `--login` and `--command` overrides. |
| `logs VM` | commands/logs.py | `os.execvp("tail", ...)` on `/var/log/libvirt/qemu/<vm>_serial.log`. `--no-follow` to print and exit. |
| `attach VM` | commands/attach.py | `os.execvp("virsh", ["virsh", "console", vm])`. Ctrl-] to detach. |

VM name → IP mapping used by `ssh` and `restart`:

| VM | IP | SSH user |
|---|---|---|
| harvester1 | 192.168.122.11 | rancher |
| harvester2 | 192.168.122.12 | rancher |
| harvester3 | 192.168.122.13 | rancher |
| rancher | 192.168.122.9 | root |

---

## Development setup

```bash
git clone https://github.com/avaleror/rodeo-cli
cd rodeo-cli
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
rodeo --help
```

To run against a local checkout of instruqt-virtualization instead of the bundled data:
```bash
export RODEO_ANSIBLE_PATH=~/instruqt-virtualization
rodeo deploy --no-tui
```

To test individual commands without a live KVM host:
```bash
rodeo init /tmp/test-rodeo
cat /tmp/test-rodeo/rodeo-plan.yaml
rodeo status --config /tmp/test-rodeo/rodeo-plan.yaml   # libvirt not found warning is expected
```

---

## Dependency versions

From `pyproject.toml`:

| Package | Constraint | Role |
|---|---|---|
| click | >=8.1 | CLI framework |
| rich | >=13.7 | Tables, progress, coloured output |
| textual | >=0.60 | TUI framework (split-panel app) |
| jinja2 | >=3.1 | Template rendering (future — not yet used in v0.2) |
| pyyaml | >=6.0 | Config + state YAML |
| requests | >=2.31 | (reserved for Rancher API calls in future) |
| ansible-core | >=2.16 | Installed via pip inside install-deps |
| libvirt-python | — | Installed as system package (python3-libvirt-python); optional import |

Ansible collections (from `rodeo/data/ansible/requirements.yml`):
- `community.general >= 8.0.0`
- `community.libvirt >= 1.3.0`
- `ansible.posix >= 1.5.0`

---

## Version history

### v0.1 (initial)
- CLI skeleton with all 10 commands registered
- `install-deps`, `init`, `status`, `clean`, `restart`, `ssh`, `logs`, `attach` fully implemented
- `deploy` used `--tags` but had wrong playbook path (`site.yml` instead of `playbook.yml`)
- `watch` was a stub
- No bundled Ansible; required `RODEO_ANSIBLE_PATH` to point at instruqt-virtualization
- Phase list: `["preflight", "kvm_host", "vms", "rancher", "finalise"]` (incorrect)

### v0.2 (current)
- Textual split-panel TUI: `DeployPanel` + `LogsPanel` with tab per VM
- TUI auto-launches when stdout is a TTY; `--no-tui` for plain Rich output
- `watch` implemented (TUI view-only mode)
- Bundled Ansible roles + deployer scripts in `rodeo/data/`
- Pipeline corrected: `kvm_host → vms → cluster → rancher → finalise`
- `cluster` phase now runs `start-vms.sh` (VIP wait + kubeconfig + 3-nodes-Ready)
- `rancher` phase now runs `setup-rancher.sh` with env vars from plan config
- `find_ansible_root()` defaults to bundled data
- `install-deps` now runs `ansible-galaxy collection install`
- Playbook path fixed: `ansible/playbook.yml`

### v0.3 (planned)
- `rodeo diagnose` — Claude API agent for log analysis and root cause suggestions
- Jinja2 plan rendering (parameters, environment overlays)
- Progress bar during VIP/kubeconfig/nodes-ready wait loops (currently just stdout lines)
- Tests (pytest) for config loading, state management, path resolution
- Make `rodeo/data/ansible/` the authoritative source; update instruqt-virtualization to sync from here

---

## Files NOT to modify without understanding the implications

| File | Why |
|---|---|
| `rodeo/data/ansible/roles/kvm_host/tasks/libvirt.yml` | Controls the libvirtd vs modular daemon setup and the NM unmanaged-devices guard — getting this wrong breaks Instruqt instance boot |
| `rodeo/data/ansible/roles/vms/tasks/network_setup.yml` | Sets virbr0 `autostart: false` intentionally — changing to true breaks Instruqt saves |
| `rodeo/data/ansible/roles/vms/defaults/main.yml` | Contains the OVMF paths, MAC addresses, and static IPs for all VMs — these are fixed by the Harvester config ISOs |
| `rodeo/data/deployer/lib/start-vms.sh` | Controls VM start order and all wait timeouts — the sequential start with a 90s gap between h2 and h3 prevents etcd join races |
| `rodeo/data/deployer/lib/setup-rancher.sh` | The Rancher import flow is delicate — the API call sequence (bootstrap → set password → get token → create cluster → apply agent manifest) has specific ordering requirements |

---

## Auditing checklist

If you're reviewing this project:

- [ ] `rodeo/config.py:_resolve_secrets()` — check that `??key` values in the plan are always resolved before being passed to Ansible (they should be in `_build_extra_vars`)
- [ ] `rodeo/engine/libvirt.py` — `undefine()` uses `VIR_DOMAIN_UNDEFINE_NVRAM` flag; verify UEFI VMs can't be undefined without it (they can't — you get a libvirtError)
- [ ] `rodeo/commands/clean.py` — glob patterns; verify they don't match files outside the image pool
- [ ] `rodeo/app.py:_run_deploy()` — the deploy worker stores `self._ansible_proc`; verify `action_quit()` always terminates it before `self.exit()`
- [ ] `rodeo/data/ansible/roles/kvm_host/tasks/packages.yml` — kubectl repo channel is `stable:/v1.36`; bump this when a new minor ships
- [ ] `rodeo/data/deployer/lib/start-vms.sh` — VIP wait is 3600s, kubeconfig fetch is 1800s, nodes-Ready is 5400s; verify these match the current Textual TUI timeouts (TUI has no separate timeout — it just streams `start-vms.sh` stdout)
