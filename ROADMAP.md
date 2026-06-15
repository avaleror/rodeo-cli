# rodeo-cli roadmap — the road to Terraform-for-labs

**Vision:** rodeo behaves like Terraform for lab deployments. A YAML plan
(or CLI parameters) declares the desired lab; `rodeo plan` shows the diff
against the host; `rodeo deploy` converges; `rodeo destroy` removes exactly
what the plan owns. The product recipes (Harvester-on-SLES16 boot ordering,
etcd gaps, Instruqt guards) stay opinionated — the *interface* is declarative.

Design pillars: plan/apply/destroy lifecycle, tfvars-style override files,
inline `-P` parameters, fail-closed validation, and ownership metadata on
libvirt objects so the hypervisor is the source of truth. Out of scope on
purpose: multi-provider abstraction — rodeo is libvirt-only and
product-opinionated by design.

Full audit behind this roadmap: see the 2026-06-12 architecture audit
(session notes); findings are tagged G (gaps), E (errors), S (simplicity),
F (efficiency) below.

---

## Phase A — Terraform UX on the current engine ✅ (done 2026-06-12)

- **G3** `-P key=value` dotted-path overrides with YAML type coercion, on
  `plan` / `deploy` / `status` / `clean` (`rodeo/commands/_options.py`)
- **G3** `--paramfile FILE` — YAML deep-merged over the plan, tfvars-style
- **G4** Jinja templating in plan files with a `parameters:` block;
  `StrictUndefined` so missing parameters are a clear error
  (precedence: profile defaults < plan < paramfile < `-P`)
- **G2** `rodeo plan` — read-only diff: VMs (`+ create` / `~ memory 8192 →
  16384` / `✓ unchanged`), network, storage artifacts, phase status; degrades
  to desired-only without libvirt; validation problems are warnings here and
  hard errors in deploy
- **E1** plain-mode progress via `rich.Status` (no more `\r` line smearing)
- **E2** `clean` keeps the shared `default` network when other VMs exist (now overridable with --force-network / --all for host reset)
- **E3** `install-deps` prints a clean message on package failures
- **E4** `ConfigError` + group-level handler: bad YAML/params show a message,
  never a traceback
- **E5** TUI quit mid-deploy exits 130
- **E6** resource sanity validation (positive ints for memory/vcpu/disk)

## Phase B — Resource ownership (next, ~2 days)

Ownership tagging: mark libvirt objects with the plan that owns them, so the
hypervisor is the source of truth, not a state file.

- [ ] Write a `rodeo:plan=<name>` marker into domain XML (`<description>` or
  `<metadata>`) via `vm.xml.j2` — one template line
- [ ] `LibvirtDriver.domains_owned_by(plan)` — query by marker
- [ ] `rodeo list` — plans on this host and the VMs each owns
- [ ] `rodeo destroy` — delete only owned domains + their disks (replaces
  clean's glob patterns; supersedes the E2 heuristic with real ownership)
- [ ] `rodeo plan` flags foreign VMs colliding with planned names
- [ ] Phase-skip logic consults actual domain existence, not just phase state
  (fixes "manually undefined VM still shows vms ✓ done")

## Phase C — Declarative inventory ✅ (largely done 2026-06-15, live-validated)

The structural change: the topology comes from the definition instead of the
Ansible role defaults hardcoding it. Done and validated end-to-end on real
SLES 16 for the `rancher` and 2-node `test` profiles.

- [x] `rodeo/inventory.py` renders `vm_nodes` from `definition.yaml`: names,
  IPs, deterministic MACs (hash of plan+node+role), `uuid5` UUIDs; generates
  missing fields, explicit values win
- [x] Rendered `vm_nodes` flow to the vars file (`DeployRunner._write_vars_file`);
  role defaults are fallback. `tests/test_ansible_consistency.py` is the contract
- [x] `ClusterPhase` derives start_order / harvester_node_names /
  harvester_ready_count / etcd_gap from the inventory — no hardcoded names; 2/3/
  N-node works. suse-virt skips the `rancher` phase when no Rancher node
- [x] **Gate:** live deploy regression done on the test host (validated the
  MAC↔DHCP↔config-ISO chain end-to-end)
- [ ] Plan schema sugar `nodes: 3` / per-VM override blocks (definition already
  supports explicit nodes; the shorthand is the remaining piece)
- [ ] Split `rancher.py` into `engine/rancher/api.py` (HTTP) + `remote.py` (SSH)
  — deferred (pure refactor, no functional gain)

## Phase D — Polish (ongoing)

Some items advanced in v0.4 (see CONTEXT.md version history): relaxed preflight for day-2 tools, attach respects plan uri, strict phase list enforcement in state/reset, SSH centralization (new `rodeo/ssh.py`), TUI VM lists now profile-driven, eject/libvirt + nodeport improvements. Big remaining P1 (topology hardcodes + full vm inventory overrides) are the core of Phase C.

- [x] **S1** (partial) kill duplicated literal defaults: SSH options and key defaults centralized in `rodeo/ssh.py` + consistent root-aware logic across engine/commands (profile/cfg still source of truth for per-plan). More literal defaults remain.
- [ ] **S2** phases return a `PhaseResult` instead of mutating
  `runner._last_rc` (removes the profile→runner private coupling)
- [x] **S3** (partial) commands call engine helpers: preflight/attach/clean use more cfg-driven logic; attach now uses libvirt uri; virsh fallbacks in stop/start/clean now pass configured libvirt uri (full consistency); several other fallbacks improved. More VIP-probe / listing duplication remains.
- [ ] **F1** cache `ansible-galaxy collection install` (marker keyed on
  requirements.yml hash)
- [ ] **F3** stream helm/K3s SSH installer output (Popen instead of buffered
  run; removes the long blind windows in the TUI)
- [ ] `--output json` for `plan` and `status` (machine-readable, CI-friendly)
- [ ] Shell completion docs (Click ships it: `_RODEO_COMPLETE=zsh_source rodeo`)
- [ ] File logging to `~/.rodeo/logs/deploy-<ts>.log` — input for `rodeo
  diagnose` (Claude API log analysis, the v0.4 headline feature)
- [ ] ansible-lint in CI; Ansible sync check lives in instruqt-virtualization's CI

---

## Standing constraints

- Do not touch the Instruqt/PXE-sensitive files without a live regression:
  `roles/kvm_host/tasks/libvirt.yml`, `roles/vms/tasks/network_setup.yml`,
  `roles/vms/defaults/main.yml`, the `pxe_server` boot chain (generic
  `boot.ipxe` → MAC-named scripts, installer cmdline, config-YAML perms), the
  etcd join gap (now applied before each additional Harvester join node), and
  the Rancher API call order. See CONTEXT.md.
- No bash deploy scripts return. Ansible stays for `kvm_host`/`vms`/`pxe_server`.
- Wall time is dominated by nested-KVM Harvester install (20-60 min);
  optimize UX and correctness first, CLI-side speed second.
- Harvester nodes need ≥ 250 GB disk (container images fill smaller persistent
  partitions → containerd fails → no VIP). Validated live.
