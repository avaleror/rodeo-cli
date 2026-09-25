---
title: Fleet claim portal, implementation plan
status: approved design, implementation not started
audience: maintainers, implementing AI or engineer
design: docs/claim-portal.md
language: en
last_updated: 2026-09-25
---

# Fleet claim portal: implementation plan

How to build [the claim portal](claim-portal.md) securely **without changing anything
that works today**. Read the design first; this file only covers order, guardrails,
tests and verification.

## 1. No-regression contract

Every phase must satisfy all of these before it merges:

1. **Opt-in only.** All new behaviour sits behind `portal.enabled: true` or
   `provider.student_access: open`. A `workshop.yaml` or `rodeo-plan.yaml` without
   those fields produces exactly the same AWS calls, security group rules, inventory
   writes and CLI output as `main` does today.
2. **Single-host AWS is untouched.** `rodeo up --target aws` / `rodeo destroy --cloud`
   share `rodeo/providers/aws.py` with fleet. They never read `student_access` or
   `portal`, and `tests/test_aws_up.py` passes unmodified.
3. **Engine untouched.** No change to phases, Ansible roles, the PXE boot chain,
   `ssh_targets.py` or the nested-hop key (see R7). Standing constraints in
   `ROADMAP.md` apply.
4. **Existing tests are not edited to pass.** New behaviour gets new tests. If an
   existing test has to change, the PR explains why the old assertion was wrong.
5. **New code in new modules.** Edits to existing files are limited to the ones listed
   per phase, and each edit is a guarded branch (`if portal enabled / if open`), not a
   rewrite.
6. **One phase, one PR**, squash-merged with a Conventional Commit title
   (`feat(fleet): ...`), `pytest` + `ruff` green, and `/security-review` run on the
   diff before merge.

## 2. Risks found in the current code

These came from reading the code on 2026-09-25. Each has an owner phase and a test
that pins it.

| # | Risk | Where | Mitigation | Phase |
|---|------|-------|------------|-------|
| R1 | `fleet deprovision` passes the lab host ids, so a portal instance would **not** be terminated, and the "anything still alive?" check before deleting the managed SG would see the portal and **leak the lab SG forever**. | `AwsProvider.deprovision`, `fleet_deprovision` | Portal gets its own SG and role tag; the still-alive check only counts instances in the same SG role; portal termination is an explicit step. | F5.2 |
| R2 | `provision()` calls `plant_rodeo_ssh_key` on **every** host it creates. Reusing it for the portal would copy the fleet-wide root key onto the one internet-facing web server. | `rodeo/providers/aws.py` provision loop | `ProvisionSpec.role`; key planting is skipped when `role == "portal"`. Test asserts `plant_rodeo_ssh_key` is never called for the portal. | F5.2 |
| R3 | `_reconcile_managed_sg_ingress` is shared by single-host `rodeo up --target aws`. A careless change opens single-host labs to the internet. | `rodeo/providers/aws.py` | New keyword argument with default equal to today's behaviour; only `fleet_provision` passes it. Characterisation test pins default rules. | F5.0 |
| R4 | There is exactly one managed SG per workshop, found by `workshop` tag. A second SG for the portal would be picked up by `_find_managed_sg` at random. | `_find_managed_sg`, `_create_managed_sg` | Portal SG tagged `rodeo-resource: portal-security-group`; lab lookup keeps filtering on `security-group`, so it can never match the portal one. | F5.2 |
| R5 | A student lab named `portal` in `hosts[]` would collide with the portal's `rodeo-host-id` tag. | `load_inventory` | Reserved id rejected, but **only** when `portal.enabled`, so no existing inventory starts failing. | F5.2 |
| R6 | Any fleet command that iterates `hosts[]` (deploy, retry, diagnose, status, access, doctor) would try to deploy a lab onto the portal. | `select_hosts` users | Portal lives in a separate top-level `portal:` block, never in `hosts[]`. Test: every fleet command's host list excludes it. | F5.2 |
| R7 | The fleet-wide rodeo private key is planted at `/root/.ssh/id_ed25519` on every lab host, and nested VMs trust it (`ssh_targets.py`, `ensure_ssh_key.yml`, workshop scripts). Root on one host means root on all hosts. The nested hop was fixed only days ago (`1d0ab91`, `91530b9`). | `ssh_key.py`, `ssh_targets.py` | **Not changed in this project.** Students never get root: `student_ssh` defaults to `false`, and when enabled the student user is not in `wheel`, `libvirt` or sudoers. A per-host key is a separate ROADMAP item that needs a live `rodeo ssh host/vm` regression. | F5.4 |
| R8 | `portal publish` reads `secrets.yaml` over SSH. If that output flows into a job file, `fleet diagnose` bundle or Rich error message, passwords leak to disk or terminal. | `fleet/ssh_exec.py`, `job.py`, `diagnose.py` | Publish uses its own call path that never logs stdout; errors print host id + exit code only. Test: a fake secret string never appears in captured logs, job files or exceptions. | F5.3 |
| R9 | `student_access: open` exposes Harvester/Rancher logins. A hand-set weak password (`rodeo set-password`) would be exposed. | `secretgen.py` | Strength gate (16+ chars, upper, lower, digit) before publish and in `fleet deploy` when `open`. | F5.0, F5.3 |

## 3. Phases

Ordered by risk: the phases that only add new code come before the phases that touch
the AWS provider's create/delete paths.

### F5.0 Baseline + student network access

Smallest useful change: students can reach their labs from any network. Useful even
before the portal exists (instructor can still use `fleet access`).

- **Baseline first (separate commit):** characterisation tests for today's behaviour
  that this project must not change:
  - SG rules for default config: exactly the operator `/32` on `22`, `8443`, `30002`
    (extends `tests/test_aws_managed_sg.py`).
  - Deprovision target selection and SG cleanup condition (R1).
  - `plant_rodeo_ssh_key` called once per created lab host (R2).
  - `load_inventory` accepts every bundled example and `docs/examples/workshop.md`
    snippet unchanged.
- **Change:** `_reconcile_managed_sg_ingress(..., open_ports=())`. Desired state per
  port: operator `/32`, plus `0.0.0.0/0` for ports in `open_ports`. Only
  `fleet_provision` computes `open_ports`, from `provider.student_access` and
  `lab.components` (`22` is never included in this phase).
- **Change:** `load_inventory` validates `provider.student_access: operator | open`
  (default `operator`); unknown values fail closed.
- **Change:** password strength check helper in `secretgen.py`
  (`is_strong_password`), used by `fleet deploy` post-check when `open`: a weak host
  is reported as failed with a clear message, not silently exposed.
- **Warning:** `fleet provision` prints the exposed ports and hosts when `open`.
- **Tests:** operator to open, open to operator (rules revoked), idempotent re-run
  makes no AWS calls, BYO `security_group_ids` untouched, single-host path never opens.
- **Live:** 1-host fleet in eu-north-1, `student_access: open`; `:8443` reachable from
  a phone on mobile data; flip back to `operator`, re-provision, unreachable again.

### F5.1 Portal service (local only, new code)

Zero risk to existing behaviour: only new files.

- `rodeo/portal/db.py`: sqlite schema (`labs`, `claims`, `invites`, `settings`),
  migrations by `user_version`, WAL mode, every query parameterised.
- `rodeo/portal/claims.py`: pure functions for claim logic. Assignment in a single
  `BEGIN IMMEDIATE` transaction. Tokens and PINs stored as SHA-256 / scrypt hashes;
  comparisons with `hmac.compare_digest`.
- `rodeo/portal/server.py`: `ThreadingHTTPServer`, routes `/`, `/claim`, `/l/<token>`,
  `/healthz`. Jinja2 with autoescape on.
- `rodeo/portal/admin.py` + `rodeo portal admin ...` commands (JSON in/out on stdin
  and stdout, so secrets never touch argv or shell history).
- `rodeo portal serve --dev` for local testing on `127.0.0.1` over HTTP.
- **Web security baseline:** CSRF token on every POST, `SameSite=Strict; HttpOnly;
  Secure` cookie, CSP `default-src 'self'`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, HSTS (behind Caddy),
  per-IP rate limit on `/claim`, body size limit, access log without tokens, emails,
  PINs or passwords.
- **Tests:** claim flows (roster, open, both, re-claim, wrong PIN, labs exhausted,
  closed), 50-thread concurrent claim race (each lab assigned exactly once), CSRF
  rejected, rate limit, token revocation, log scrubbing, templates escape input.

### F5.2 Portal VM lifecycle (touches the AWS provider)

Highest-risk phase; all four provider risks (R1, R2, R4, R5, R6) are closed here.

- `ProvisionSpec.role: str = "lab"`; `role="portal"` skips `plant_rodeo_ssh_key`,
  tags `rodeo-role: portal`, uses its own SG (`443`, `80` open; `22` operator only).
- `rodeo/fleet/portal.py`: provision/deprovision orchestration, merges into
  `workshop.yaml` `portal:` block (new `merge_portal` helper; `merge_provisioned_hosts`
  untouched).
- `fleet provision`: after the lab hosts succeed, provision the portal if enabled.
  A portal failure never rolls back or fails the lab hosts; it reports and exits
  non-zero so a re-run converges.
- `fleet deprovision`: terminate labs, then portal (unless `--keep-portal`), then
  each SG only when nothing in *its own role* is still alive.
- `rodeo fleet portal up`: over SSH, install rodeo at `lab.ref`, create the
  `rodeo-portal` system user, systemd unit with hardening, Caddy (repo package if
  Leap 16 has it, else pinned binary with SHA-256), enable, wait for `/healthz` over
  HTTPS. Idempotent.
- **Tests:** everything in R1, R2, R4, R5, R6 as named assertions; provision with
  portal disabled makes identical calls to baseline (snapshot of mocked EC2 calls).
- **Live:** provision a 1-host fleet with portal, valid Let's Encrypt cert on
  `portal-<ip>.sslip.io`, `ssh root@portal` shows no `/root/.ssh/id_ed25519`;
  deprovision leaves zero instances and zero SGs (verify with the kill-switch sweep in
  `~/rodeo-aws-tests/`).

### F5.3 Publish and instructor commands

- `rodeo fleet portal publish`: per ready host, read `secrets.yaml` via a dedicated
  non-logging SSH call (R8), run the strength gate (R9), build the lab record from
  `fleet/access.py` URL logic, push to the portal via `rodeo portal admin publish` on
  stdin. Not-ready hosts published as `building`. Upserts never touch assignments.
- `invite`, `info`, `status`, `open`, `close`, `rotate-code`, `release`, `reassign`,
  `revoke`, `export`, all with `--output json`.
- `fleet status` gains a `student` column **only** when `portal.enabled`.
- **Tests:** R8 leak test, publish idempotency (twice gives same DB), release rotates
  token, invite CSV is 0600, JSON schemas stable.
- **Live (end to end):** 2-host fleet, one roster student, one event-code student,
  each sees only their own lab and logs into Harvester; release and re-claim works;
  `fleet retry` on a host followed by `publish` updates the card.

### F5.4 Student SSH (optional, off by default)

Only if the workshop exercises need a shell on the KVM host (design, open question 2).

- `portal.student_ssh: true` creates, idempotently over SSH, a `student` user with no
  sudo and no `wheel`/`libvirt` membership, a per-host ed25519 key generated on the
  laptop and pushed only to that host and its portal record. Opens `22` to the internet
  (key-only; `ssh_pwauth: false` is already in the EC2 userdata).
- **Tests:** generated user has no sudo, is in no privileged group, cannot read
  `/root` (asserted by the remote script's own checks), key is unique per host.
- **Live:** student can SSH to own host, cannot to another, `sudo -n true` fails,
  `cat /root/.ssh/id_ed25519` fails.

### F5.5 Docs and release

- `docs/fleet.md` F5 section with the instructor flow, `docs/examples/workshop.md`
  snippet, CHANGELOG via release-please, ROADMAP checkboxes.
- Add the per-host nested key (R7) as its own ROADMAP item.

## 4. Security review gates

For every phase PR:

- `/security-review` on the diff; findings fixed or explicitly accepted in the PR.
- `pip-audit` (already in CI), gitleaks (pre-commit, global).
- No new runtime dependency in `pyproject.toml` core `dependencies`.
- Grep gate: no `print`/`console.print`/logger call receives a variable holding a
  password, token, PIN or email (reviewed by hand, plus the R8 test).

Before the first real workshop: one end-to-end run where a second person (not the
instructor) tries to reach another student's lab from their own claim, and fails.

## 5. Live test budget

All live tests in **eu-north-1** (eu-west-1 has no internet gateway in the SUSE
account). Smallest instance type that runs the lab profile under test, fleets of 1-2
hosts, torn down the same day, kill-switch scripts in `~/rodeo-aws-tests/` armed before
each run.

## 6. Out of scope

- Replacing the fleet-wide nested-hop key (R7), tracked separately.
- GCP/Vultr/Hetzner portal support (comes free with F4b-d via `HostProvider`, verified
  when those land).
- Email sending, SSO, web admin UI, proxying lab traffic.
