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
- **E2** `clean` keeps the shared `default` network when other VMs exist
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

## Phase C — Declarative inventory (~4-5 days + live KVM regression)

The structural change: the plan declares the VM topology instead of the
Ansible role defaults hardcoding it. Prerequisite for variable node counts
and custom networks; folds in old surgical tasks 9 and 10.

- [ ] `rodeo/inventory.py` renders `vm_nodes` from the plan: names, count,
  IPs (base+offset or explicit), deterministic MACs (hash of plan+name),
  `uuid5` UUIDs
- [ ] Pass rendered `vm_nodes` via the vars file; role defaults become
  fallback only (`tests/test_ansible_consistency.py` is the migration
  contract — update both sides together)
- [ ] Plan schema: `nodes: 3` or explicit per-VM blocks; ClusterPhase derives
  start order and Ready count from inventory instead of hardcoded names
- [ ] Split `rancher.py` into `engine/rancher/api.py` (HTTP) and
  `engine/rancher/remote.py` (SSH) while it grows inventory awareness
- [ ] **Gate:** full deploy regression on geekohive before merge — this
  touches the MAC↔DHCP↔config-ISO chain (see CONTEXT.md fragile files)

## Phase D — Polish (ongoing)

Some items advanced in v0.4 (see CONTEXT.md version history): relaxed preflight for day-2 tools, attach respects plan uri, strict phase list enforcement in state/reset, SSH centralization (new `rodeo/ssh.py`), TUI VM lists now profile-driven, eject/libvirt + nodeport improvements. Big remaining P1 (topology hardcodes + full vm inventory overrides) are the core of Phase C.

- [x] **S1** (partial) kill duplicated literal defaults: SSH options and key defaults centralized in `rodeo/ssh.py` + consistent root-aware logic across engine/commands (profile/cfg still source of truth for per-plan). More literal defaults remain.
- [ ] **S2** phases return a `PhaseResult` instead of mutating
  `runner._last_rc` (removes the profile→runner private coupling)
- [x] **S3** (partial) commands call engine helpers: preflight/attach/clean use more cfg-driven logic; attach now uses libvirt uri; several virsh fallbacks improved. More VIP-probe / listing duplication remains.
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

- Do not touch the Instruqt-sensitive files without a live regression:
  `roles/kvm_host/tasks/libvirt.yml`, `roles/vms/tasks/network_setup.yml`,
  `roles/vms/defaults/main.yml`, the 90 s etcd join gap, the Rancher API
  call order. See CONTEXT.md.
- No bash deploy scripts return. Ansible stays for `kvm_host`/`vms` only.
- Wall time is dominated by nested-KVM Harvester install (20-60 min);
  optimize UX and correctness first, CLI-side speed second.
