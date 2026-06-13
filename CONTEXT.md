# rodeo-cli — Project Context

This document gives any developer or AI assistant enough context to audit, extend, or take over this project without needing the original conversation history.

---

## What this is

`rodeo-cli` is a Python CLI tool that deploys and manages the **SUSE Virtualization Rodeo** — an Instruqt-based training lab that runs a 3-node Harvester HCI cluster plus Rancher Prime, all inside nested KVM virtual machines on a single SLES 16 host.

It replaces `rodeo.sh`, a monolithic bash script in the parent repository. Design goals:

- One Python orchestrator (`DeployRunner`) driving Ansible for declarative host work and Python for procedural cluster work — no bash in the middle
- Structured phase tracking with per-plan state, resume (`--from PHASE`), and `--force`
- Direct libvirt-python VM operations instead of shelling out to virsh
- A Textual split-panel TUI showing deploy progress and VM serial logs side by side
- Self-contained packaging: Ansible roles ship inside the Python package
- Infra-agnostic: cloud VMs, bare metal, or Instruqt (`deployment_target` guard)

**GitHub:** https://github.com/avaleror/rodeo-cli
**Author:** Andres Valero, Principal Technology Advocate at SUSE
**Version:** 0.4.0
**Python:** 3.10+

---

## Relationship to instruqt-virtualization

`rodeo-cli` is a sibling project to `avaleror/instruqt-virtualization` (private). That repo contains the Instruqt challenge definitions and the original `rodeo.sh`. The Ansible roles in `rodeo/data/ansible/` were snapshotted from it; the intent is that rodeo-cli is now the source of truth and instruqt-virtualization syncs from here. A CI sync check is still on the backlog (belongs in the instruqt repo's CI).

The bash deployer scripts (`start-vms.sh`, `setup-rancher.sh`) were retired in v0.3: their logic lives in `rodeo/engine/cluster.py` and `rodeo/engine/rancher.py`. Only config examples remain in `rodeo/data/deployer/`.

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
├── config.py               Plan + secrets loading, ??resolvers, validation
├── state.py                Per-plan phase state at ~/.rodeo/state/<plan>.yaml
├── ssh.py                  Shared SSH options and helpers (centralized from engine/commands)
├── app.py                  Textual TUI App — thin event subscriber, no deploy logic
├── widgets/
│   ├── deploy_panel.py     Left panel: DataTable phases + progress bar + RichLog
│   └── logs_panel.py       Right panel: TabbedContent VM serial logs
├── engine/
│   ├── runner.py           DeployRunner — single pipeline, yields typed events
│   ├── cluster.py          ClusterPhase — VM start order, VIP/kubeconfig/nodes waits
│   ├── rancher.py          RancherPhase — K3s, cert-manager, Rancher, import
│   └── libvirt.py          LibvirtDriver — direct libvirt-python VM/network ops
├── profiles/
│   ├── base.py             RodeoProfile ABC: phases, vm_names, guarded_phases
│   └── suse_virt.py        suse-virt profile (the only one today)
├── commands/               One file per CLI command (thin: load config, run engine)
└── data/
    ├── ansible/            Bundled roles: kvm_host + vms + pxe_server (iPXE boot)
    ├── deployer/           inventory.local + legacy config examples (no scripts)
    └── templates/          rodeo-plan.yaml + secrets.yaml templates for init
```

### Event-driven pipeline

`DeployRunner.run()` is a generator yielding typed events (`PhaseStarted`, `PhaseSkipped`, `PhaseDone`, `PhaseFailed`, `LogLine`, `ProgressUpdate`, `DeployComplete`). The TUI (`app.py`) and plain mode (`commands/deploy.py:_deploy_plain`) consume the same stream; neither contains pipeline logic.

Failure semantics:
- Every phase runs inside an exception boundary; any exception becomes `PhaseFailed` and stops the pipeline.
- `runner.stop` (a `threading.Event`) is checked by every poll loop in ClusterPhase/RancherPhase; `runner.terminate()` sets it and SIGTERMs the current subprocess group, so quitting the TUI interrupts multi-hour waits.
- Subprocess timeouts in RancherPhase are converted to failed results (rc 124/127), never exceptions.

### Profiles

A profile defines `phases`, `vm_names`, `ansible_phases`, `guarded_phases`, default config, and phase dispatch. `suse-virt` is the only profile; the scaffold exists for future rodeo types. CLI commands resolve VM names and phase lists from the loaded plan/profile, never from hardcoded lists.

---

## Deployment pipeline

Six phases, tracked per plan in `~/.rodeo/state/<plan-name>.yaml`. Idempotent; resume with `--from PHASE`, re-run all with `--force`.

| Phase | Engine | What it does |
|---|---|---|
| kvm_host | Ansible (`--tags kvm_host`) | Packages, modular libvirt daemons, NM unmanaged conf, firewalld permanent rules + DNAT, storage pool |
| vms | Ansible (`--tags vms`) | ISO/qcow2 downloads, NAT network with static leases, disks, OVMF vars, Harvester config ISOs, Rancher cloud-init ISO, domain XML with disk-first boot order (does not start VMs) |
| pxe_server | Ansible (`--tags pxe_server`) | nginx on virbr0:8080, ipxe.efi TFTP, vmlinuz/initrd/rootfs, per-node iPXE scripts + config YAMLs, dnsmasq two-stage UEFI boot |
| cluster | Python `ClusterPhase` | Start firewalld; ensure virbr0 up; start harvester1, poll VIP (≤60 min); start h2, 90 s etcd gap, h3, rancher; fetch kubeconfig via SSH; wait 3 nodes Ready (≤90 min) |
| rancher | Python `RancherPhase` | Wait SSH; install K3s, Helm, cert-manager, Rancher Prime; NodePort 30002; set admin password via API; import Harvester cluster; CoreDNS zone patch; eject install ISOs |
| finalise | Python (runner) | `set_autostart` on all VMs (fails if zero succeed) + enable libvirt-guests |

### Instruqt guard

`deployment_target: instruqt` in the plan skips `finalise` (a profile `guarded_phase`) unless `rodeo deploy --finalise` is passed. Running finalise before the Instruqt image snapshot breaks instance boot (libvirt-guests stalls network-online.target). After the snapshot: `rodeo deploy --from finalise --finalise`. On `baremetal` (default), finalise runs normally.

---

## Configuration system

### rodeo-plan.yaml (working directory)

Generated by `rodeo init`. Keys: `name`, `type` (profile), `deployment_target`, `network` (mode/vip/rancher_ip/gateway/dns_domain), `resources` (memory/vcpu/disk per flavor), `versions` (harvester/rancher/k3s/cert_manager), `storage.image_dir`, `libvirt.uri`, `ansible` (path/inventory), `credentials`.

Plans support Jinja templating with a `parameters:` block (StrictUndefined — missing parameters are a clear `ConfigError`). Overrides: `--paramfile FILE` deep-merges a YAML over the plan (tfvars-style); `-P key=value` sets dotted config paths (YAML-coerced) and feeds template parameters. Precedence: base defaults < profile defaults < plan < paramfile < `-P`. All user-facing config errors are `ConfigError`, caught at the CLI group level (message, not traceback).

All plan keys are wired: network and resources/versions reach Ansible through a chmod-600 vars file (`~/.rodeo/rodeo-vars-*.yaml`, passed as `-e @file`, deleted on exit, stale ones swept at startup). `versions.rancher/k3s/cert_manager` and `network.gateway` are consumed directly by RancherPhase.

### Secrets

`~/.rodeo/secrets.yaml` (chmod 600). `rodeo init` writes a random 16-char password ( `--ask` for hidden collection, `$RODEO_PASSWORD` for CI/Instruqt) and a random `harvester_token` (cluster join token). 


`??` placeholders in the plan resolve at load time and **fail closed** (leftover `??` aborts deploy via `validate_config`, as do empty/CHANGE_ME credentials):

| Form | Source |
|---|---|
| `??key` | `~/.rodeo/secrets.yaml` |
| `??env:NAME` | environment variable |
| `??file:/path` | file contents |
| `??cmd:command` | command stdout (pass, op, vault CLI) |

Ansible tasks that render password-bearing files run with `no_log: true` and mode 0600.

### Ansible root resolution (`config.py:find_ansible_root()`)

1. `cfg['ansible']['path']` → 2. `RODEO_ANSIBLE_PATH` env → 3. bundled `rodeo/data/` → 4. cwd → 5. `~/instruqt-virtualization`.

---

## Key SLES 16 constraints

These are the non-obvious things that burned time during development:

1. **No wicked — NetworkManager only.** All network config goes through NM. The kvm_host role marks virbr0/vnet* unmanaged via `/etc/NetworkManager/conf.d/99-libvirt-unmanaged.conf`.
2. **Modular libvirt daemons, not monolithic libvirtd.** Socket-activated virtqemud/virtnetworkd/etc. The kvm_server pattern's post-install scripts re-enable `libvirtd.service`; the role explicitly disables it (it breaks Instruqt boot).
3. **Instruqt boot failure root cause.** libvirtd/libvirt-guests starting at boot activates virbr0/dnsmasq before cloud-init finishes, stalling network-online.target. Both stay disabled during build; libvirt-guests is enabled only in finalise (post-snapshot).
4. **OVMF path.** 4MB non-SecureBoot variants only: `ovmf-x86_64-4m-{code,vars}.bin` from `qemu-ovmf-x86_64`.
5. **xorriso not genisoimage** for all ISO building.
6. **Nested KVM is slow.** Harvester install takes 20-60 min; VIP timeout is 3600 s, nodes-Ready 5400 s (class constants on `ClusterPhase`).
7. **kubectl** comes from the upstream repo `pkgs.k8s.io/core:/stable:/v1.36/` (channel duplicated in `install_deps.py` and `roles/kvm_host/defaults/main.yml` — bump both).

---

## Commands reference

| Command | Notes |
|---|---|
| `install-deps` | Root required. Distro packages (zypper/apt/dnf) for KVM stack + ansible-core (pip fallback) + kubectl repo + collections. |
| `init [DIR] [--ask] [--force]` | Plan from template; secrets with random password + token. `$RODEO_PASSWORD` honoured. |
| `plan [-P k=v] [--paramfile F]` | Read-only diff of desired vs actual: VMs (create/change/unchanged via libvirt dom.info), network, storage artifacts, phases. Degrades to desired-only without libvirt. Validation issues are warnings here, hard errors in deploy. |
| `deploy [--from P] [--force] [--check] [--finalise] [--tui/--no-tui] [-P k=v]` | Full pipeline. `--check` = preflight only (root, /dev/kvm, nested virt, RAM, disk, tools). |
| `clean [--yes] [--all --secrets --force-network --hard]` | Per-plan or full host reset: destroy rodeo VMs + default network + artifacts + all/specific plan states + optional secrets. Leaves packages + rodeo binary (for fresh test or node repurposing). Runs stop first unless --hard. |
| `stop [--yes] [--all]` | Graceful infra-aware stop (definition: infra_type, components, reverse start_order; VM ACPI shutdown + host services). Restartable. |
| `start [--yes] [--all]` | Start after stop (host services + VMs in order + wait). |
| `generate [--dir] [--advanced]` | Generator for custom definition + full config-dir skeleton (from templates base via parameter collection; produces yaml with infra_type etc for stop/start/clean; validation + next steps for bootstrap/deploy). Supports declarative model entry point. |
| `status` | VM table (from plan inventory), VIP probe, phase progress. |
| `watch` | TUI view-only (TTY required). |
| `restart VM\|all [--hard]` | ACPI shutdown + start; VM names from plan. |
| `ssh VM [-l user] [-c cmd]` | IP/user from plan `vms`; key from `ssh.identity_file`. |
| `logs [VM] [--bundle -o FILE]` | Tail serial log, or write a support bundle (state + redacted config + serial log tails). |
| `attach VM` | virsh console (Ctrl-] to detach; honors `libvirt.uri` from plan). |

---

## Testing & CI

`tests/` (pytest, no KVM needed): config/resolvers/validation, per-plan state, full runner event flow against a fake profile (failure stop, exception boundary, skip/force/from, instruqt guard, cancellation, vars file), cluster wait loops with mocked urllib/subprocess, rancher timeout safety, init password/token generation.

GitHub Actions (`.github/workflows/ci.yml`): ruff + pytest on Python 3.10 and 3.12.

Dev setup:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check rodeo tests && pytest tests/
```

For end-user / clean-host testing use the bootstrap script (see README and user-guide):
```bash
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/scripts/bootstrap-sles.sh | bash
```
```

Also update ROADMAP if it mentions setup, but for time, skip if not critical.

Now, update the Generated content files in the Claude workspace to reflect the final state (since user said "update all the docs and files").

The test doc, plan, assessment we have been updating via previous replaces.

Also, perhaps add a new high-quality doc or enhance.

Now, for diagrams, we created one.

To deliver high quality, perhaps create a new top-level docs or enhance user-guide further with more Mermaid.

Also, add comments to other code files? The user said "add comments to the code if they´re not present" — focus on the new bootstrap related code, which we did.

Existing code may have comments, but to be thorough, perhaps add to cli.py quickstart or something, but since many, focus on bootstrap.

Now, time to push.

First, update the Claude Generated docs to be consistent.

We already did some replaces on them.

Let's make sure the test doc in Generated is up to date with the curl flow (we did a replace earlier).

Also, commit the Claude workspace changes.

First, stage and commit in rodeo-cli.

Use terminal for git operations.

Since push uses the direct github url as before.
The repo has a global gitleaks pre-commit hook; dummy passwords in tests need `# gitleaks:allow`.

---

## Version history

- **v0.1** — CLI skeleton, wrong playbook path, no bundled Ansible, phantom preflight phase.
- **v0.2** — Textual TUI, bundled Ansible + bash deployer scripts, corrected 5-phase pipeline.
- **v0.3** — Bash retired (ClusterPhase/RancherPhase in Python), single event-driven DeployRunner, per-plan state, preflight `--check`, instruqt finalise guard, secrets resolvers (`??env/file/cmd`), random init credentials, exception/timeout/cancellation hardening, pytest suite + CI, profile scaffold.
- **v0.4 (current)** — Dynamic `__version__` from package metadata; centralized SSH options in `rodeo/ssh.py` + consistent key defaults; profile-driven VM lists in TUI (no hardcoded tabs/switches); removed global `state.PHASES` (profiles own phase lists, `reset_from` now strictly requires phases arg); relaxed preflight (`--check`: virsh/ssh are warnings only, core tools remain required); `attach` respects `libvirt.uri`; Cluster/Rancher use derived users + libvirt URI; eject uses LibvirtDriver with stop support; NodePort patch uses explicit `--type strategic`; libvirt driver uses constants. P0/P1 safe items complete (big inventory/topology deferred to roadmap Phase C per constraints).
- **v0.4+ (planned)** — `rodeo diagnose` (Claude API log analysis), ansible-lint in CI, ansible sync check with instruqt-virtualization, streaming output for long SSH installs, full declarative inventory (Phase C).

---

## Files NOT to modify without understanding the implications

| File | Why |
|---|---|
| `rodeo/data/ansible/roles/kvm_host/tasks/libvirt.yml` | Modular daemon setup + NM unmanaged guard — breaks Instruqt boot if wrong |
| `rodeo/data/ansible/roles/vms/tasks/network_setup.yml` | virbr0 `autostart: false` is intentional during build |
| `rodeo/data/ansible/roles/vms/defaults/main.yml` | OVMF paths, fixed MACs and static IPs baked into Harvester config |
| `rodeo/data/ansible/roles/pxe_server/` | iPXE boot chain — sync from test-harv-rodeo when changing PXE behavior |
| `rodeo/engine/cluster.py` start order + timeouts | Sequential start with 90 s h2→h3 gap prevents etcd join races |
| `rodeo/engine/rancher.py` API call order | bootstrap login → change password → re-login → server-url → create cluster → registration token → apply manifest is order-dependent |

Drift guards: `tests/test_ansible_consistency.py` pins the role defaults, the Python profile, and the plan template to each other (VM names/IPs, VIP, gateway, flavors, versions, MAC/UUID uniqueness) — if you change one source on purpose, change all three and the test tells you where. `validate_config()` additionally rejects VIP/node-IP collisions and rancher_ip mismatches in user-edited plans at deploy time.
