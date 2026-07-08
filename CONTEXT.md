# rodeo-cli — Project Context

This document gives any developer or AI assistant enough context to audit, extend, or take over this project without needing the original conversation history.

---

## What this is

`rodeo-cli` is a Python CLI that deploys and manages **rodeos** — live, hands-on labs on nested KVM. It is **Terraform-for-labs**: a declarative `definition.yaml` + `rodeo-plan.yaml` describe the desired lab; `rodeo plan` diffs it against the host; `rodeo deploy` converges. Two profiles ship today: `suse-virt` (3-node Harvester HCI + Rancher Prime, also a 2-node `test` variant) and `rancher` (Rancher Prime on K3s, single VM, no Harvester). A third, `suse-edge` (SUSE Edge 3.6 + Elemental + EIB), is in development on the `feature/suse-edge` branch. It runs on bare metal, Instruqt, or cloud VMs with nested KVM.

It replaces `rodeo.sh`, a monolithic bash script in the parent repository. Design goals:

- One Python orchestrator (`DeployRunner`) driving Ansible for declarative host work and Python for procedural cluster work — no bash in the middle
- Structured phase tracking with per-plan state, resume (`--from PHASE`), and `--force`
- Direct libvirt-python VM operations instead of shelling out to virsh
- A Textual split-panel TUI showing deploy progress and VM serial logs side by side
- Self-contained packaging: Ansible roles ship inside the Python package
- Infra-agnostic: cloud VMs, bare metal, or Instruqt (`deployment_target` guard)
- Beginner on-ramp: `rodeo up` (one command: doctor → pick a lab → secrets → deploy → login) with file-based secrets and sudo self-escalation (no `source`/`-E`)

**GitHub:** https://github.com/avaleror/rodeo-cli
**Author:** Andres Valero, Principal Technology Advocate at SUSE
**Version:** 0.11.5 <!-- x-release-please-version --> (bundled profiles live-validated on bare metal SLES 16; Instruqt validation pending — see ROADMAP)
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
│   ├── rancher.py          RancherPhase — K3s, cert-manager, Rancher, NodePort, eject ISOs
│   └── libvirt.py          LibvirtDriver — direct libvirt-python VM/network ops
├── inventory.py            build_inventory(): renders definition.yaml → vm_nodes (MAC/UUID gen), pxe, firewall, host_prep
├── config_dir.py           --config-dir (EIB-style) loader
├── preflight.py            Host detect + run_preflight + recommend_profile (doctor/up/check)
├── secretgen.py            Shared password/token generation + ~/.rodeo/secrets.yaml
├── labseed.py              Seed a lab from a profile; resolve/scaffold custom profiles
├── privilege.py            sudo self-escalation (ensure_root)
├── success.py              Topology-aware success screen
├── profiles/
│   ├── base.py             RodeoProfile ABC: phases, vm_names, guarded_phases
│   ├── suse_virt.py        suse-virt profile (Harvester + Rancher; conditional rancher phase)
│   └── rancher.py          rancher profile (Rancher Prime on K3s, no Harvester)
├── commands/               One file per CLI command (thin: load config, run engine)
└── data/
    ├── ansible/            Bundled roles: kvm_host + vms + pxe_server (iPXE boot)
    ├── profiles/<type>/definition.yaml   Bundled declarative topology per profile
    ├── examples/           harvester / harvester-lab-config (test) / rancher-lab-config
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

A profile defines `phases`, `vm_names`, `ansible_phases`, `guarded_phases`, default config, and phase dispatch. Two ship today, a third is in development:
- **`suse-virt`** — `kvm_host → vms → pxe_server → cluster → rancher → finalise`. Harvester HCI + Rancher. The `rancher` phase is skipped when the topology has no Rancher node (e.g. the 2-node `test` lab).
- **`rancher`** — `kvm_host → vms → boot → rancher → finalise`. One VM = Rancher Prime on K3s, no Harvester (no pxe_server/cluster). The lightweight `boot` phase starts the network + VM (the suse-virt `cluster` phase does that for Harvester).
- **`suse-edge`** (in development, `feature/suse-edge` branch) — SUSE Edge 3.6: Rancher Prime + Elemental Operator + EIB + 3 edge nodes with vTPM 2.0. sslip.io + Let's Encrypt for the Rancher URL.

CLI commands resolve VM names and phase lists from the loaded plan/profile, never from hardcoded lists. Topology (start order, node names, ready count, etcd gap) comes from the definition via `inventory.build_inventory()`, not hardcoded strings.

**Profiles as a CLI concept (`--profile`):** distinct from the engine `type`. `--profile <name>` selects a *runnable config-dir* — a bundled example (`rancher`/`test`/`harvester`) or a custom one under `~/.rodeo/profiles/<name>` created with `rodeo new`. See `docs/custom-rodeos.md`.

---

## Deployment pipeline

Six phases, tracked per plan in `~/.rodeo/state/<plan-name>.yaml`. Idempotent; resume with `--from PHASE`, re-run all with `--force`.

| Phase | Engine | What it does |
|---|---|---|
| kvm_host | Ansible (`--tags kvm_host`) | Packages, modular libvirt daemons, NM unmanaged conf, firewalld permanent rules + DNAT, storage pool |
| vms | Ansible (`--tags vms`) | ISO/qcow2 downloads, NAT network with static leases, disks, OVMF vars, Harvester config ISOs, Rancher cloud-init ISO, domain XML with disk-first boot order (does not start VMs) |
| pxe_server | Ansible (`--tags pxe_server`) | nginx on virbr0:8080, ipxe.efi TFTP, vmlinuz/initrd/rootfs, **one generic `boot.ipxe`** + per-node scripts named by MAC, per-node config YAMLs (0644), dnsmasq two-stage UEFI boot |
| boot (rancher profile) | Python (runner) | Lightweight: start firewalld + network + the defined VMs (no Harvester VIP/etcd waits) |
| cluster | Python `ClusterPhase` | **Topology-driven from the definition** (start_order, harvester_node_names, harvester_ready_count, etcd gap). Start firewalld; virbr0 up; start the bootstrap node; poll VIP (≤60 min); start remaining nodes in order with the etcd gap before each additional join node; fetch kubeconfig via SSH; wait `ready_count` nodes Ready (≤90 min). Works for 2-node, 3-node, N-node. |
| rancher | Python `RancherPhase` | Wait SSH; install K3s, Helm, cert-manager, Rancher Prime; NodePort 30002; set admin password via API (idempotent; tolerates empty API bodies); eject ISOs. Harvester cluster import is a lab exercise — not automated. |
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
6. **Nested KVM is slow.** Harvester install takes 20-60 min; VIP timeout is 3600 s, nodes-Ready 5400 s (class constants on `ClusterPhase`). With adequate disk, a 2-node cluster converged in ~19 min on a 16 vCPU / 62 GiB host.
7. **kubectl** comes from the upstream repo `pkgs.k8s.io/core:/stable:/v1.36/` (channel duplicated in `install_deps.py` and `roles/kvm_host/defaults/main.yml` — bump both).
8. **`python3-lxml` is required** — the `community.libvirt` Ansible modules (vms phase) import it. In install-deps; checked in preflight/doctor.
9. **`guestfs-tools` (`virt-customize`) is required** — the Leap 16 Minimal-VM cloud image ships **without cloud-init**, so the vms role injects it into the Rancher disk at build time. (The rancher VM's NoCloud seed ISO is otherwise ignored.)
10. **Harvester disk ≥ 250 GB.** The elemental install carves the disk into OS + a ~50 GB Longhorn partition + the persistent partition that holds container images (~19 GB). At 100 GB the persistent partition is only ~27 GB → containerd "no space left on device" → RKE2 never converges → no VIP. The `test` profile uses 250 GB; the full profile 270 GB.
11. **Installer console logging.** The VM's file-backed serial is `ttyS1`; the Harvester installer cmdline sets `console=ttyS1 console=ttyS0` so the kernel/dracut output is captured in `*_serial.log` (and a console-only `ttyS0` pty can block kernel writes → boot hang).
12. **`sudo rodeo` caveat (unfixed):** SLES sudo `secure_path` excludes `/usr/local/bin`, so `sudo rodeo <cmd>` fails "command not found". `rodeo up` self-escalates with the absolute path; day-2 root commands (clean/stop/start) should do the same (backlog).

---

## Commands reference

| Command | Notes |
|---|---|
| `up [--profile N] [--name] [--dir] [--yes] [--no-deploy]` | **Start here.** Doctor → pick a lab that fits RAM → file-based secrets → sudo self-escalation → deploy → success screen. Resolves bundled + custom profiles. |
| `doctor` | Read-only host report (RAM/CPU/disk/KVM/nested virt/tools/python: libvirt+lxml) + which profile fits. |
| `new <name> --from <base>` | Scaffold an editable custom profile under `~/.rodeo/profiles/<name>` (copies a working bundled lab). Deploy with `rodeo up --profile <name>`. |
| `profiles` | List bundled + custom profiles. |
| `install-deps` | Root required. Distro packages (zypper/apt/dnf) for KVM stack + ansible-core + kubectl repo + collections + python3-lxml + guestfs-tools. |
| `bootstrap` / `generate` | (advanced) Clean-SLES one-command setup / interactive config-dir generator. New users prefer `up` / `new`. |
| `init [DIR] [--ask] [--force] [--profile rancher\|test\|harvester]` | Plan from template or seed from a profile; secrets with random password + token. `$RODEO_PASSWORD` honoured. |
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

For end-user / clean-host testing, the simplest path is `rodeo up` (or the bootstrap script). The global **gitleaks** pre-commit hook means dummy passwords in tests need a `# gitleaks:allow` comment.

Live SLES 16 testing has covered the `rancher` and 2-node `test` profiles end-to-end (see Version history). The full 3-node `harvester` profile has not yet been run live.

---

## Version history

- **v0.1** — CLI skeleton, wrong playbook path, no bundled Ansible, phantom preflight phase.
- **v0.2** — Textual TUI, bundled Ansible + bash deployer scripts, corrected 5-phase pipeline.
- **v0.3** — Bash retired (ClusterPhase/RancherPhase in Python), single event-driven DeployRunner, per-plan state, preflight `--check`, instruqt finalise guard, secrets resolvers (`??env/file/cmd`), random init credentials, exception/timeout/cancellation hardening, pytest suite + CI, profile scaffold.
- **v0.4** — Dynamic `__version__` from package metadata; centralized SSH options in `rodeo/ssh.py` + consistent key defaults; profile-driven VM lists in TUI (no hardcoded tabs/switches); removed global `state.PHASES` (profiles own phase lists, `reset_from` now strictly requires phases arg); relaxed preflight (`--check`: virsh/ssh are warnings only, core tools remain required); `attach` respects `libvirt.uri`; Cluster/Rancher use derived users + libvirt URI; eject uses LibvirtDriver with stop support; NodePort patch uses explicit `--type strategic`; libvirt driver uses constants; added tests for generate/stop/start; generate no longer silently clobbers global secrets (exists check + warning); virsh fallbacks in stop/start/clean now honor plan libvirt.uri. P0/P1 safe items complete (big inventory/topology deferred to roadmap Phase C per constraints).
- **v0.5 (june-lifecycle tag)** — `generate` + `stop`/`start` (infra-aware from definition) + `clean --all/--hard/--secrets/--force-network` host reset + `bootstrap` + `--config-dir` (EIB-style) + `init --profile`. Versioning moved to git tags.
- **v0.6** — **Beginner on-ramp**: `rodeo up` (doctor → fit a lab → file secrets → self-sudo → deploy → success), `rodeo doctor`, file-based secrets default, lab-dir auto-detect. **`rancher` profile** (Rancher Prime on K3s, single VM) + lightweight `boot` phase. **Declarative custom rodeos**: `rodeo new` / `rodeo profiles`, profiles resolve from `~/.rodeo/profiles/`; `docs/custom-rodeos.md`. **Phase C (topology-driven)**: ClusterPhase reads start_order/harvester_node_names/harvester_ready_count/etcd_gap from the definition; conditional rancher phase; 2/3/N-node works. **Hardened by live SLES 16 testing** (both profiles validated end-to-end): python3-lxml + preflight check; cloud-init injected into the Leap 16 image (`virt-customize`); MAC-based iPXE chain (per-host dnsmasq tags were silently ignored vs libvirt host entries); installer console logged to the serial file; Harvester config YAMLs 0644 (nginx 403); mgmt interface by MAC not eth0; **Harvester disk 250 GB** (100 GB filled → containerd no-space → no VIP); topology-aware success screen; RancherPhase tolerates empty API bodies + idempotent password.
- **v0.9.0** — Harvester import dropped from automation (now an Instruqt lab exercise); custom TLS cert generation removed; Rancher setup ends after NodePort + admin API + ISO eject; `start-if-needed` idempotent boot guard; 20-min cap on background Rancher drain loop; OVMF async copy fix (`command: cp` instead of `copy` with async).
- **v0.9.1** — `rodeo self-update` command (git pull + pip reinstall in one shot); `rodeo clean` auto-refreshes the CLI at the end of every run.
- **v0.10.x (current)** — declarative inventory (Phase C) live-validated on bare metal; bundled `harvester-2n` + `suse-edge` profiles added (3 engine types: `rancher`, `suse-virt`, `suse-edge`); profile standardization — shared config assembly + phase dispatch centralized in `profiles/base.py`, the three profile classes reduced to data + deltas (PR #4); numerous Harvester-import and Rancher bootstrap fixes.
- **planned** — full 3-node `harvester` live regression; `sudo rodeo` self-escalation for clean/stop/start; `rodeo diagnose` (Claude API log analysis); ansible-lint + ansible sync check in CI; `--output json`; finish wiring `vm_nodes` overrides + `nodes: N` shorthand; split `rancher.py` into api/remote.

---

## Files NOT to modify without understanding the implications

| File | Why |
|---|---|
| `rodeo/data/ansible/roles/kvm_host/tasks/libvirt.yml` | Modular daemon setup + NM unmanaged guard — breaks Instruqt boot if wrong |
| `rodeo/data/ansible/roles/vms/tasks/network_setup.yml` | virbr0 `autostart: false` is intentional during build |
| `rodeo/data/ansible/roles/vms/defaults/main.yml` | OVMF paths, fixed MACs and static IPs baked into Harvester config |
| `rodeo/data/ansible/roles/pxe_server/` | iPXE boot chain (generic `boot.ipxe` → MAC-named per-node scripts), config-YAML perms (0644), installer kernel cmdline (console=ttyS1, root=live squashfs, hwAddr mgmt interface). All validated live — change carefully. |
| `rodeo/engine/cluster.py` start order + timeouts | Topology-driven (definition), but the bootstrap-first → VIP → join-with-etcd-gap sequence and VIP/Ready timeouts prevent etcd join races; change with a live regression |
| `rodeo/engine/rancher.py` API call order | bootstrap login → change password → re-login → server-url → create cluster → registration token → apply manifest is order-dependent |

Drift guards: `tests/test_ansible_consistency.py` pins the role defaults, the Python profile, and the plan template to each other (VM names/IPs, VIP, gateway, flavors, versions, MAC/UUID uniqueness) — if you change one source on purpose, change all three and the test tells you where. `validate_config()` additionally rejects VIP/node-IP collisions and rancher_ip mismatches in user-edited plans at deploy time.
