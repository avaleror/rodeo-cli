# rodeo-cli roadmap

**Vision:** rodeo behaves like Terraform for lab deployments. A YAML plan declares the desired lab; `rodeo plan` shows the diff against the host; `rodeo deploy` converges; `rodeo destroy` removes exactly what the plan owns. The product recipes (Harvester boot ordering, etcd gaps, Instruqt guards) stay opinionated — the interface is declarative.

Design pillars: plan/apply/destroy lifecycle, tfvars-style override files, inline `-P` parameters, fail-closed validation, and ownership metadata on libvirt objects so the hypervisor is the source of truth. Out of scope on purpose: multi-provider abstraction. rodeo is KVM-first and product-opinionated by design.

---

## Validation queue — Instruqt (current priority)

These items are done and live-validated on bare metal (SLES 16). None have been tested on an Instruqt builder instance yet. This is the gate before any Instruqt track ships.

| Item | Status | Risk |
|------|--------|------|
| `test` profile end-to-end on Instruqt builder (2-node Harvester, `deployment_target: instruqt`) | pending | medium — iPXE chain + Instruqt cloud-init layout differ from bare metal |
| `harvester` profile on Instruqt (full lab: 3-node + Rancher) | pending | high — 90+ min deploy inside Instruqt's nested KVM limit; RAM ceiling is tight |
| `harvester-ha` profile on Instruqt | pending | medium — same chain as test but 3 nodes |
| `rancher` profile on Instruqt | pending | low — no PXE, fast deploy, but cloud-init inject on Leap 16 image needs verification |
| libvirt network hook (DNAT fix, b5c1421) on Instruqt's nftables stack | pending | high — Instruqt manages `eth0` via NM; hook may conflict with their network setup |
| `rodeo deploy --from finalise --finalise` after Instruqt snapshot | pending | medium — core of the Instruqt workflow; not tested since the finalise refactor |
| Student tab routing (:90, :91, :92 via `cloud-client` nginx proxy) | pending | medium — lives in `instruqt-virtualization`, not rodeo-cli; depends on VIP being reachable |
| `deployment_target: instruqt` firewalld-disabled guard on SLES 16 Instruqt image | pending | low — logic exists, not validated on current Instruqt SLES 16 image variant |

**What to do:** Run a full builder deploy for each profile on Instruqt, take a snapshot, boot an attendee instance, and verify the cluster is reachable from student tabs. Fix whatever breaks before marking complete.

---

## Phase A — Terraform UX ✅ (done 2026-06-12)

- `-P key=value` dotted-path overrides with YAML type coercion on `plan` / `deploy` / `status` / `clean`
- `--paramfile FILE` — YAML deep-merged over the plan, tfvars-style
- Jinja templating in plan files with a `parameters:` block; `StrictUndefined` so missing parameters fail loudly
- `rodeo plan` — read-only diff: VMs (+ create / ~ memory 8192 → 16384 / ✓ unchanged), network, storage, phase status
- Plain-mode progress via `rich.Status`, `ConfigError` group handler (no tracebacks on bad YAML)
- `clean` keeps shared `default` network unless `--force-network` or `--all`
- Resource sanity validation (positive ints for memory/vcpu/disk)

## Phase B — Resource ownership (next, ~2 days)

Ownership tagging: mark libvirt objects with the plan that owns them, so the hypervisor is the source of truth, not a state file.

- [ ] Write `rodeo:plan=<name>` into domain XML (`<description>`) via `vm.xml.j2`
- [ ] `LibvirtDriver.domains_owned_by(plan)` — query by marker
- [ ] `rodeo list` — plans on this host and the VMs each owns
- [ ] `rodeo destroy` — delete only owned domains + their disks (replaces `clean`'s glob patterns)
- [ ] `rodeo plan` flags foreign VMs colliding with planned names
- [ ] Phase-skip logic consults actual domain existence, not just phase state

## Phase C — Declarative inventory ✅ (done 2026-06-15, live-validated on bare metal)

- `rodeo/inventory.py` renders `vm_nodes` from `definition.yaml`: names, IPs, deterministic MACs, `uuid5` UUIDs
- `ClusterPhase` derives start_order / harvester_node_names / harvester_ready_count / etcd_gap from the inventory — N-node works
- `suse-virt` skips `rancher` phase when no Rancher node in the topology
- Bundled profiles: `rancher` (1 VM), `test` (2-node), `harvester-ha` (3-node HA), `harvester` (3-node + Rancher)
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
- [ ] `clean` / `stop` / `start` self-escalate with sudo (same as `up` — needed for SLES `secure_path`)
- [ ] `--output json` for `plan` and `status` (machine-readable, CI-friendly)
- [ ] Cache `ansible-galaxy collection install` (marker keyed on `requirements.yml` hash)
- [ ] Stream Helm/K3s SSH installer output (removes the long blind windows in the TUI)
- [ ] `PhaseResult` return type instead of mutating `runner._last_rc`
- [ ] ansible-lint in CI

---

## Phase E — Cloud targets: AWS / GCP

Add `deployment_target: aws` and `deployment_target: gcp` so rodeo-cli can provision a KVM host automatically, not just consume one that is already set up.

Approach: the KVM host itself becomes an EC2 / GCE instance. rodeo provisions it (or reuses an existing one), installs KVM + rodeo deps via `install-deps`, then runs the normal lab pipeline on top. The nested VMs are the same — only the outer host changes.

- [ ] `aws` provider in a new `rodeo/providers/` layer: create/reuse a metal or nitro-virt EC2 instance with KVM enabled
- [ ] `install-deps` support for Amazon Linux 2023 (dnf path already exists, needs testing)
- [ ] Security group rules mirror the firewalld rules (ports 8443, 30002, 22)
- [ ] `storage.device` detection on NVMe instance store (`/dev/nvme1n1`)
- [ ] `rodeo up --target aws --instance-type m7i.metal-24xl` as the front door
- [ ] Cost guard: `rodeo plan` estimates on-demand hourly cost for the selected instance type
- [ ] `rodeo destroy` terminates the instance (opt-in; off by default to prevent accidents)
- [ ] GCP equivalent: Compute Engine with `--enable-nested-virtualization` on N2 / C3

**Prerequisite for AWS:** Phase B (ownership tagging) must land first so `destroy` can clean up cloud resources safely.

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
- [ ] `suse-edge` profile skeleton in `rodeo/data/examples/suse-edge-config/`
- [ ] Engine support for Elemental node boot (cloud image, not PXE)
- [ ] `rancher` phase extended: install Elemental Operator via Helm after Rancher
- [ ] `cluster` phase variant: wait for edge node registration, not iPXE install
- [ ] Live validation on a SLES 16 host before any Instruqt track

**Dependency:** Phase C must be fully stable (including Instruqt validation) before this starts.

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

## Standing constraints

- Do not touch the Instruqt/PXE-sensitive files without a live regression: `roles/kvm_host/tasks/libvirt.yml`, `roles/vms/tasks/network_setup.yml`, `roles/vms/defaults/main.yml`, the `pxe_server` boot chain (generic `boot.ipxe` → MAC-named scripts, installer cmdline, config-YAML perms), the etcd join gap (applied before each additional Harvester join node), and the Rancher API call order. See CONTEXT.md.
- No bash deploy scripts. Ansible stays for `kvm_host` / `vms` / `pxe_server`.
- Wall time is dominated by nested-KVM Harvester install (20–60 min). Optimize UX and correctness first, CLI speed second.
- Harvester nodes need at least 250 GiB disk. Smaller disks fill the persistent partition, containerd fails, VIP never comes up. Validated live.
- Phases E–G (cloud targets, new rodeos) do not start until the Instruqt validation queue is clear and the current profiles are proven stable.
