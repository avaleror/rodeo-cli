# rodeo-cli roadmap

**Vision:** rodeo behaves like Terraform for lab deployments. A YAML plan declares the desired lab; `rodeo plan` shows the diff against the host; `rodeo deploy` converges; `rodeo destroy` removes exactly what the plan owns. The product recipes (Harvester boot ordering, etcd gaps, Instruqt guards) stay opinionated — the interface is declarative.

Design pillars: plan/apply/destroy lifecycle, tfvars-style override files, inline `-P` parameters, fail-closed validation, and ownership metadata on libvirt objects so the hypervisor is the source of truth. Out of scope on purpose: multi-provider abstraction. rodeo is KVM-first and product-opinionated by design.

---

## Validation queue — Instruqt ✅ (validated 2026-07-15)

Live-validated on bare metal (SLES 16) and on an **Instruqt builder** with `deployment_target: instruqt`. Deploy converges end-to-end (iPXE / DNAT / firewalld guard / nested KVM). Nested performance is slow as expected — size hosts generously (≈40 host vCPU helps) and keep guest vCPU sum under ~70% of the host.

| Item | Status | Notes |
|------|--------|------|
| `test` profile end-to-end on Instruqt builder (2-node Harvester, `deployment_target: instruqt`) | ✅ done | Nested KVM; same iPXE chain as fuller Harvester labs |
| `harvester` profile on Instruqt (full lab: 3-node + Rancher) | ✅ done | Validated; tune `resources.harvester` (e.g. 10 vCPU / 20 GiB) on larger builders |
| `harvester-ha` profile on Instruqt | ✅ done | Same install chain as `test` / `harvester` (3-node HA) |
| `rancher` profile on Instruqt | ✅ done | No PXE; cloud-init path verified on current Instruqt SLES/Leap layout |
| libvirt network hook (DNAT fix, b5c1421) on Instruqt's nftables stack | ✅ done | Works with Instruqt-managed `eth0` / NM |
| `rodeo deploy --from finalise --finalise` after Instruqt snapshot | ✅ done | Core snapshot → attendee autostart path |
| Student tab routing (:90, :91, :92 via `cloud-client` nginx proxy) | follow-up | Lives in `instruqt-virtualization`, not rodeo-cli; confirm per track when publishing |
| `deployment_target: instruqt` firewalld-disabled guard on SLES 16 Instruqt image | ✅ done | Guard holds on current Instruqt SLES 16 image |

**Instruqt performance (nested-KVM wall-clock):**
- ✅ Guest disk cache defaults keyed on target — `writeback`/`threads` on instruqt, `none`/`native` on baremetal, plan-overridable (`libvirt.disk_cache` / `disk_io`). Merged #42.
- ✅ Host-aware guest resource presets on seed — keeps Σ guest vCPU ≈ 70% of host so nested installs don't thrash; soft warning in `doctor`/preflight (#43).
- ⏳ Hauler prefetch (skip multi-GB re-downloads) — still Phase H below.

**Remaining workshop polish:** confirm student tabs in the sibling Instruqt track repo when a track ships.

---

## Phase A — Terraform UX ✅ (done 2026-06-12)

- `-P key=value` dotted-path overrides with YAML type coercion on `plan` / `deploy` / `status` / `clean`
- `--paramfile FILE` — YAML deep-merged over the plan, tfvars-style
- Jinja templating in plan files with a `parameters:` block; `StrictUndefined` so missing parameters fail loudly
- `rodeo plan` — read-only diff: VMs (+ create / ~ memory 8192 → 16384 / ✓ unchanged), network, storage, phase status
- Plain-mode progress via `rich.Status`, `ConfigError` group handler (no tracebacks on bad YAML)
- `clean` keeps shared `default` network unless `--force-network` or `--all`
- Resource sanity validation (positive ints for memory/vcpu/disk)

## Phase B — Resource ownership + drift-aware reconciliation (next)

Today, idempotency is **phase-level, not resource-level**: `DeployRunner.run()`
skips any phase already marked `completed` in `~/.rodeo/state/<name>.yaml`
(`runner.py:192`), regardless of whether the live host still matches the plan.
`rodeo plan` already computes the real diff (VM memory/vcpu vs. live libvirt,
network active/inactive, storage artifacts present) — `rodeo deploy`/`rodeo up`
just never look at it. Closing that gap is the actual "Terraform apply" promise
in the vision statement at the top of this file; B1 and B2 below are the concrete
steps.

### B1 — Ownership tagging (~2 days)

Mark libvirt objects with the plan that owns them, so the hypervisor is the source of truth, not a state file.

- [ ] Write `rodeo:plan=<name>` into domain XML (`<description>`) via `vm.xml.j2`
- [ ] `LibvirtDriver.domains_owned_by(plan)` — query by marker
- [ ] `rodeo list` — plans on this host and the VMs each owns
- [ ] `rodeo destroy` — delete only owned domains + their disks (replaces `clean`'s glob patterns)
- [ ] `rodeo plan` flags foreign VMs colliding with planned names

### B2 — Auto-reconciliation: phase-skip consults live state, not just the state file (~1 week, opt-in rollout)

The gap: edit `resources.harvester.memory_mib` (or add a node) in an already-deployed
custom profile, re-run `rodeo up --profile <name>` — nothing happens, because the `vms`
phase is cached `completed: true` and `runner.run()` never checks whether the live VM
still matches. `rodeo plan` shows the drift; `rodeo deploy` ignores it. See
`docs/custom-rodeos.md#re-running-after-a-deploy` for the user-facing version of this gap
and today's manual workaround (`--force` / `--from` / `clean` + redeploy).

**Step 1 — extract the diff, don't duplicate it.** ✅ Pull the drift-detection logic
currently living in `plan_cmd.py` (`_inspect_host`, `_print_vms`'s memory/vcpu compare,
`_print_network`, `_print_storage`) into a shared module (e.g. `rodeo/drift.py`) that
returns a structured `DriftReport` (which phases are affected and why), not print
statements. `plan_cmd.py` becomes a renderer over that report — pure refactor, no
behavior change, existing `test_plan_cmd.py` cases must still pass unmodified.

**Step 2 — scope V1 narrowly: VM memory/vcpu drift on already-defined domains only.** ✅
This is the one case already diffed, tested, and well understood. Explicitly out of
scope for V1: topology changes (add/remove a node — Harvester join/etcd-gap sequencing
makes a safe partial re-run a much bigger problem, see Standing Constraints below) and
anything touching `pxe_server`/`cluster` (the fragile, live-regression-only phases).
Those stay on manual `--force`/`--from` until a V2 is scoped separately.

**Step 3 — wire it into `DeployRunner.run()`, opt-in first.** ✅ Add `--reconcile` to
`rodeo deploy`/`rodeo up`. When set, before the existing `is_phase_done()` skip check,
consult the shared `DriftReport`; if it flags the `vms` phase, call the existing
`state.reset_from("vms", plan_name, profile.phases)` instead of skipping — reuses
machinery that already exists for `--from`, just triggered by drift instead of a flag.
Print what was detected and what's about to re-run before doing it (never a silent
surprise) — reuse the same rendering `plan` already has.

**Step 4 — add `test_runner.py` coverage**: ✅ mock the shared drift module reporting VM
memory drift and assert `DeployRunner.run()` re-runs `vms` (not skipped) while leaving
unrelated already-done phases skipped, and that omitting `--reconcile` reproduces
today's exact behavior (no regressions for anyone not opting in).

**Step 5 — live-validate, then flip the default.** Run `--reconcile` through a real
memory-resize-and-redeploy cycle on a bare-metal KVM host (matches this repo's existing
"validate live before shipping" convention — see Standing Constraints). Only once that's
proven: make reconciliation the default behavior and add `--no-reconcile` as the opt-out,
matching the vision statement's "declare desired state ... converge" without a flag.

**Step 6 — V2 (separate, later, own roadmap entry when scoped):** topology drift
(new/removed nodes) that needs Harvester join/etcd-gap sequencing, and broader
pxe/cluster reconcile. **Partial progress:** NAT DHCP host reservations
(`mac+name+ip`) are now checked in Ansible guards and in ``--reconcile`` via
`rodeo/drift.py` (still requires VMs stopped to redefine the network).

## Phase C — Declarative inventory ✅ (done 2026-06-15, live-validated on bare metal)

- `rodeo/inventory.py` renders `vm_nodes` from `definition.yaml`: names, IPs, deterministic MACs, `uuid5` UUIDs
- `ClusterPhase` derives start_order / harvester_node_names / harvester_ready_count / etcd_gap from the inventory — N-node works
- `suse-virt` skips `rancher` phase when no Rancher node in the topology
- Bundled profiles across 3 engine types (`rancher`, `suse-virt`, `suse-edge`): `rancher` (1 VM), `test` (2-node), `harvester-ha` (3-node HA), `harvester-2n` (2-node + Rancher), `harvester` (3-node + Rancher), `suse-edge` (Rancher + Elemental + EIB + 4 edge nodes)
- [ ] Plan schema sugar `nodes: 3` shorthand (explicit node blocks already work; shorthand is the remaining piece)

## Phase D — Polish (ongoing)

- [x] SSH options centralized in `rodeo/ssh.py`; consistent root-aware logic across commands
- [x] libvirt network hook for DNAT-allow (host:8443 → Harvester VIP) — bare metal validated
- [x] File logging to `~/.rodeo/logs/<plan>.log` — subprocess output teed in real time
- [x] TUI: split-view serial logs — all VM consoles visible simultaneously (removed tab switching)
- [x] TUI: ANSI stripping + 200ms batching — fixes screen corruption from iPXE/kernel escape sequences
- [x] TUI: global deploy timer (left panel) + per-VM elapsed timers in each console window
- [x] VM state heartbeat file (`~/.rodeo/logs/<lab>-heartbeat.txt`) — written every 5 min during VIP/node waits; lets you tell a stuck install from an Instruqt timeout at a glance
- [x] `deployment_target` wiring — `baremetal|instruqt` drives NIC pinning, success screen URLs, and firewall rules; auto-detected and persisted to lab plan
- [x] Harvester install fix: ISO-seed config uses MAC-based NIC matching (`hwAddr`) instead of `name: eth0` — fixes hang on kernels with `net.ifnames=1`
- [x] VIP enforcement — Harvester UI and kubeconfig always reference the cluster VIP, not individual node IPs
- [x] `rodeo up` re-run: `--target` persisted to existing lab plan; no spurious interactive prompt when target is already known
- [x] Profile standardization (PR #4, 2026-07-06): shared config assembly and phase dispatch centralized in `profiles/base.py` (`STORAGE_DEFAULT`, `BASE_VERSIONS`, table-driven `run_phase`, definition-load-with-fallback `default_cfg`); the three profile classes reduced to data + deltas (−103 lines). Fixed a latent aliasing bug — `default_cfg()` now deep-copies so in-place config merges can no longer corrupt shared class defaults. Deploy config verified byte-identical for all 6 bundled profiles.
- [ ] `clean` / `stop` / `start` self-escalate with sudo (same as `up` — needed for SLES `secure_path`)
- [x] `--output json` for `doctor` and `status` (machine-readable; fleet F0) — `plan` JSON still open
- [x] Cache `ansible-galaxy collection install` (marker keyed on `requirements.yml` hash)
- [ ] Stream Helm/K3s SSH installer output (removes the long blind windows in the TUI)
- [ ] `PhaseResult` return type instead of mutating `runner._last_rc`
- [x] ansible-lint in CI

---

## Phase E — Cloud targets: AWS / GCP

Add `deployment_target: aws` and `deployment_target: gcp` so rodeo-cli can provision a KVM host automatically, not just consume one that is already set up.

Approach: the KVM host itself becomes an EC2 / GCE instance. From the laptop, `rodeo up --target aws` provisions (or reuses) it, waits for SSH, then remote-runs `rodeo up --target baremetal` on the instance. Nested VMs are unchanged — only the outer host is cloud-acquired.

- [x] `aws` provider in `rodeo/providers/` — create/reuse by ownership tags (`ManagedBy=rodeo`)
- [x] Shared `provider:` block in `rodeo-plan.yaml` (same shape as Fleet `workshop.yaml`)
- [x] `rodeo up --target aws` — provision + SSH + remote deploy (MVP)
- [x] `rodeo destroy --cloud --yes` — terminate ownership-tagged single-host instance
- [x] Infra adaptation (`rodeo/host_context.py`): aws `disk_gb` floor 1200, `storage.backend: nvme`, kvm_host NVMe → `image_dir`; nested virt default on for non-metal
- [x] Instance tiers (`budget` / `recommended` / `performance`) per lab profile + region offerings/capacity DryRun (`instance_catalog.py`, `assert_available`); `rodeo up --instance-tier`
- [ ] `install-deps` support for Amazon Linux 2023 (dnf path already exists, needs testing) — prefer SLES 16 / Leap 16 AMIs
- [ ] Security group rules mirror the firewalld rules (ports 8443, 30002, 22) — still operator-supplied SG
- [ ] Cost guard: `rodeo plan` estimates on-demand hourly cost for the selected instance type
- [ ] Live validate: `i7i.8xlarge` Leap 16 (or SLES 16) + NVMe pool + harvester (provision or BYO) — checklist in [docs/examples/testing.md](docs/examples/testing.md#aws-live-smoke-i7i8xlarge--nvme)
- [ ] GCP equivalent: Compute Engine with `--enable-nested-virtualization` on N2 / C3

**Share with Fleet F4:** same `rodeo/providers/aws` backs `rodeo fleet provision` and single-host `rodeo up --target aws`. Remaining providers: GCP → Vultr Bare Metal → Hetzner Cloud (see Phase I). Equinix is out of scope.

**Sizing:** three tiers per profile (see `rodeo/providers/instance_catalog.py`); **recommended** for Harvester is **`i7i.8xlarge`** (local NVMe). Explicit `provider.instance_type` still wins. Metal remains the performance escape hatch. Tiny types are rejected at validate. Guest disks default to **1.2 TiB per Harvester node** under aws host-context. Availability is checked in-region before create; no silent downsize.

---

## Phase F — SUSE Edge Rodeo

A new bundled profile (`suse-edge`) for SUSE Edge workshops. SUSE Edge = RKE2 + Rancher + Elemental + Edge Image Builder + optionally Metal3 for bare-metal provisioning of simulated edge nodes.

The rodeo deploys a management plane (Rancher + Rancher Prime) plus one or more simulated edge clusters, all as nested KVM VMs. Attendees practice deploying and updating edge nodes from the Rancher UI without needing real hardware.

**Target stack:**
- 1 management VM: K3s + Rancher Prime + Elemental Operator
- 2–3 edge VMs: RKE2 clusters registered to Rancher (auto-imported via Elemental)
- Optionally: 1 Metal3 + Ironic VM for bare-metal provisioning simulation

**What makes it different from the current `harvester` profile:**
- No iPXE / Harvester ISO — edge VMs boot from a pre-baked Elemental image
- Registration workflow: edge nodes call home to Rancher, not the other way around
- Upgrade demo: Elemental OS upgrade shown live in the workshop

**Milestones:**
- [x] `suse-edge` profile shipped and merged to `main` — registered engine type sharing the standardized `RodeoProfile` base (`rodeo/data/platforms/suse-edge/`, `rodeo/profiles/suse_edge.py`)
- [x] Engine support for Elemental node boot (cloud image, not PXE) — `boot` phase in place of the Harvester `pxe_server`/`cluster` phases
- [x] `rancher` phase extended: dedicated `elemental` phase installs the Elemental Operator via Helm after Rancher
- [ ] `cluster` phase variant: wait for edge node registration, not iPXE install (edge nodes are currently a started-by-hand lab exercise)
- [ ] Live validation on a SLES 16 host before any Instruqt track (`suse-edge` specifically — base Instruqt queue is clear)

**Dependency:** Phase C and the Instruqt builder queue (top of file) are clear; remaining gate is `suse-edge` live validation above.

---

## Phase G — SUSE Telco Cloud Rodeo

A new bundled profile (`telco-cloud`) for SUSE Telco Cloud workshops. SUSE Telco Cloud = SUSE Virtualization (Harvester) as the infrastructure layer, plus a 5G RAN simulation stack (Open5GS or OAI) running as CNFs on top.

Attendees practice deploying and operating a telco workload on HCI infrastructure — scaling vDU/vCU functions, observability, and lifecycle management — without physical RAN hardware.

**Target stack:**
- 3-node Harvester cluster (reuses the `harvester-ha` base)
- 1 RKE2 workload cluster imported into Harvester
- CNF workloads: Open5GS core + simulated gNB (UERANSIM) deployed via Helm
- Monitoring: Prometheus + Grafana pre-loaded with telco dashboards

**What makes it different from the current profiles:**
- Workload layer on top of HCI — the lab is about what runs on Harvester, not Harvester itself
- Two-tier deploy: infrastructure (Harvester) then workloads (CNFs)
- RAN simulation adds strict timing/CPU constraints — VMs need pinned CPUs and no overcommit

**Milestones:**
- [ ] `telco-cloud` profile skeleton; Harvester base reused from `harvester-ha`
- [ ] New `workloads` phase in the pipeline: Helm installs on the RKE2 guest cluster
- [ ] CPU pinning support in `resources:` block (`vcpu_pinning: true`)
- [ ] Open5GS + UERANSIM Helm charts validated on nested KVM (performance baseline)
- [ ] Grafana dashboards for RAN metrics (UE attach, throughput, latency)
- [ ] Live validation on bare metal before Instruqt track

**Dependency:** Phases E and F must be stable; workloads phase design depends on Phase C inventory being fully proven.

---

## Phase H — Air-gap / Disconnected environments (Hauler integration)

Hauler (https://github.com/hauler-dev/hauler) is a single Go binary (~31 MB) from Rancher Government that collects container images, Helm charts, and files into a portable OCI bundle, then serves them via an embedded registry and fileserver. It is the standard SUSE/Rancher approach for disconnected deployments.

Hauler manifest format (content.hauler.cattle.io/v1):
- `kind: Images` — container images with optional platform and cosign verification
- `kind: Charts` — Helm charts from HTTP repos or OCI registries
- `kind: Files` — arbitrary files from URLs or local paths (ISOs, cloud images, install scripts)

Workflow: `hauler store sync -f manifest.yaml` → `hauler store save -f haul.tar.zst` → transport → `hauler store load` → `hauler store serve registry` + `hauler store serve fileserver`.

This phase has three levels of increasing scope. Each level is independent and can ship on its own.

**Level 1 — Prefetch phase: cache binary artifacts (low effort, high return)**

Use Hauler to pre-download the large binary artifacts (Harvester ISO, Leap 16 images) before the `vms` phase. The fileserver runs on localhost and the Ansible roles pull from there instead of the internet. This solves the IPv6/errno 101 download failures on Instruqt and avoids re-downloading 2+ GB on every `rodeo clean && rodeo up`.

- [ ] Add `hauler` binary install to `install-deps` (or download in the phase itself)
- [ ] Add a Hauler manifest template per platform in `rodeo/data/platforms/<name>/hauler-files.yaml` (Files kind only — ISOs and cloud images)
- [ ] New `prefetch` phase (before `vms`): runs `hauler store sync`, `hauler store save`, starts `hauler store serve fileserver` as a background process
- [ ] Parameterise `leap16_url` and `harvester_iso_url` in Ansible defaults to honour `http://localhost:8080/` when the fileserver is running
- [ ] Guarded by a plan flag (`prefetch: true`) so existing deploys are unaffected by default
- [ ] Live validation: `harvester` profile on Instruqt with `prefetch: true`; confirm ISO + image pulled from localhost

**Level 2 — `deployment_target: airgap` (medium effort)**

A new deployment target for environments with no external internet access. The operator pre-builds a `haul.tar.zst` on a connected machine and transfers it to the disconnected host. rodeo-cli loads it and serves all content locally before running the normal phase pipeline.

Plan additions:
```yaml
deployment_target: airgap
airgap:
  haul_path: /path/to/haul.tar.zst
  registry: localhost:5000
  fileserver: http://localhost:8080
```

- [ ] Hauler manifest templates per platform covering all container images, Helm charts, and binary files needed end-to-end
- [ ] `kvm_host` phase extended: install hauler, `hauler store load`, start registry + fileserver as systemd services
- [ ] Ansible Rancher role: set `global.cattle.systemDefaultRegistry` to `localhost:5000`
- [ ] Harvester config YAML: point containerd mirror to `localhost:5000` (registry mirrors config)
- [ ] Harvester PXE / iPXE chain: ISO and kernel/initrd pulled from fileserver, not the internet — this is the hardest part; requires changes to `pxe_server` role and boot templates
- [ ] `hauler store save` helper command (`rodeo bundle --profile harvester`) for the connected-side prep step
- [ ] Live validation on bare metal: full `harvester` deploy with all external network blocked

**Level 3 — Instruqt offline bake-in (medium effort, highest workshop value)**

Bake a pre-loaded Hauler store into the geekohive snapshot (`suse-virt-rodeo-180`) so the builder run needs no external internet. This makes the builder faster (no 2 GB+ downloads during the 2-3 h build window) and immune to upstream outages or bandwidth throttling at venues.

- [ ] Depends on Level 1 being validated
- [ ] Add a `rodeo bundle` step to the builder track (`01-build/assignment.md`) that runs on a connected machine and produces `haul.tar.zst`
- [ ] Builder step: load the haul into the geekohive image before the `vms` phase
- [ ] Hauler bundle versioned alongside platform versions — bump both together when Harvester or Rancher version changes
- [ ] Document the connected-side prep workflow for SUSE PTA team (Andres + Raul)

**Dependencies:** Level 1 can start independently. Level 2 requires Level 1 validated. Level 3 requires Level 2. Instruqt builder validation (top of this file) is clear as of 2026-07-15.

---

## Phase I — Fleet / workshop fan-out

Laptop-side orchestration that runs the same single-host `rodeo` on many KVM hosts
over OpenSSH (workshop inventory). Engine/phases stay single-host. See [docs/fleet.md](docs/fleet.md).

- [x] F0 — `rodeo doctor|status --output json` via `rodeo/service/`
- [x] F1 — `workshop.yaml` + `rodeo fleet doctor|status` (OpenSSH fan-out, labels, concurrency)
- [x] F2 — `rodeo fleet deploy|retry|access` (tmux start, job file, failed-only retry, URL sheet)
- [x] F2.1 — `rodeo fleet diagnose` (central log/status collect for failure forensics)
- [ ] F3 — MCP tools on top of fleet (after F2)
- [ ] F4 — Host-acquire → `workshop.yaml` (separate from converge); Python native SDKs behind `rodeo/providers/`
  - [x] F4a — **AWS** MVP (`boto3`, optional `[aws]` extra): `fleet provision|deprovision`, ownership tags, inventory merge — gaps: no plan dry-run; deprovision does not clear `hosts[]`
  - [ ] F4b — **GCP** (`google-cloud-compute`) — after AWS
  - [ ] F4c — **Vultr Bare Metal** (API `/v2/bare-metals`, optional `[vultr]` extra) — after GCP
  - [ ] F4d — **Hetzner Cloud** (`hcloud`) — after Vultr; nested-KVM must be validated for labs
  - Equinix Metal: **out of scope** (service sunset)

---

## Standing constraints

- Do not touch the Instruqt/PXE-sensitive files without a live regression: `roles/kvm_host/tasks/libvirt.yml`, `roles/vms/tasks/network_setup.yml`, `roles/vms/defaults/main.yml`, the `pxe_server` boot chain (generic `boot.ipxe` → MAC-named scripts, installer cmdline, config-YAML perms), the etcd join gap (applied before each additional Harvester join node), and the Rancher API call order. See CONTEXT.md.
  - `roles/vms/tasks/network_setup.yml` / `pxe_server/tasks/network.yml`: redefine is
    guarded on a **per-node** DHCP host marker (`mac+name+ip`), not merely “some
    `<host mac=` exists”. `--reconcile` also consults live network XML for the same
    marker (see `rodeo/drift.py`). Still prefer a live regression after touching
    PXE boot-chain files (generic `boot.ipxe` → MAC-named scripts, installer cmdline,
    config-YAML perms).
- No bash deploy scripts. Ansible stays for `kvm_host` / `vms` / `pxe_server`.
- Wall time is dominated by nested-KVM Harvester install (20–60 min). Optimize UX and correctness first, CLI speed second.
- Harvester nodes need at least 250 GiB disk. Smaller disks fill the persistent partition, containerd fails, VIP never comes up. Validated live.
- Phases E–G (cloud targets, new rodeos) may proceed now that Instruqt builder validation is clear; still prefer proving each new platform on bare metal before an Instruqt track ships.
