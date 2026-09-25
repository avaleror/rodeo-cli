---
title: Fleet claim portal (students get their own lab)
status: approved design (2026-09-25), implementation not started
plan: docs/claim-portal-plan.md
audience: maintainers, implementing AI or engineer
repo: https://github.com/avaleror/rodeo-cli
related:
  - docs/fleet.md
  - docs/n8n-integration.md
  - rodeo/fleet/access.py
  - rodeo/providers/aws.py
  - rodeo/ssh_key.py
  - rodeo/secretgen.py
language: en
last_updated: 2026-09-25
---

# Fleet claim portal

## 1. Problem

`rodeo fleet provision` + `rodeo fleet deploy` give the instructor N working labs,
but students cannot get into them today:

| Gap | Where | Effect |
|-----|-------|--------|
| Ingress is operator-only | `_reconcile_managed_sg_ingress` in `rodeo/providers/aws.py`, `MANAGED_SG_PORTS = (22, 8443, 30002)` | The managed SG allows exactly the operator's `/32` and revokes anything else. Students time out. |
| Credentials never leave the host | `rodeo/fleet/access.py` (URLs only), `~/.rodeo/secrets.yaml` per host | Nothing hands a student their password. |
| No student identity | `workshop.yaml` `hosts[]` | No record of who has which lab; the instructor cannot find "Maria's lab" when she asks for help. |
| Only one SSH key exists, and it is fleet-wide | `plant_rodeo_ssh_key` copies `~/.rodeo/ssh/id_ed25519` to `/root/.ssh/` on **every** host | Anyone with root on one lab host can read a key that is root on all of them. Students must never get root on the KVM host. |

## 2. Decisions already made (Andrés, 2026-09-25)

1. **Self-service claim portal** (option B), not printed handouts.
2. **Runs on a dedicated small VM**, provisioned and destroyed with the fleet. Not on a
   lab host, not on the laptop, not provider-specific serverless.
3. **Both claim modes**, chosen per workshop: roster (personal links) and open
   (event code + email).
4. Design doc first, then phased implementation.
5. **Students need zero network knowledge.** Lab UI ports are opened to the internet
   (`provider.student_access: open`) with safeguards, instead of IP allowlists
   (section 7).

This is a deliberate exception to rule 3 of `docs/n8n-integration.md` ("no
`rodeo serve` HTTP daemon"). That rule is scoped to the n8n Phase 0 layer. The
portal is a separate feature, and its security constraints (section 6) keep the spirit
of that plan's rule 4: no cloud credentials and no rodeo SSH key ever leave the
instructor's machine.

## 3. Goals and non-goals

**Goals**

- A student opens one URL, identifies themselves, and gets *their* lab: UI URLs,
  usernames, passwords and, optionally, SSH access. Nothing about anyone else's lab.
- The instructor sees who has which lab and can release or reassign one.
- Works for any fleet: AWS today, GCP/Vultr/Hetzner once F4b-d ship, bare metal via
  a bring-your-own portal host.
- Idempotent like the rest of rodeo: every command safe to re-run.
- No new Python dependencies in the core package.

**Non-goals (v1)**

- Sending email. The CLI outputs a CSV of personal links; the instructor mail-merges
  or n8n sends them.
- A web admin UI. Instructor control is CLI-only (smaller attack surface, no admin
  password to manage).
- SSO / OAuth login.
- Proxying lab UIs through the portal. Students connect to their lab host directly.

## 4. Architecture

```
  Instructor laptop                    Portal VM (per workshop)             Lab hosts
  -----------------                    ------------------------             ---------
  rodeo fleet provision  ──creates──►  t3.small, tag rodeo-role=portal
                         ──creates──────────────────────────────────────►  host-01..N
  rodeo fleet deploy     ──SSH──────────────────────────────────────────►  rodeo up
  rodeo fleet portal publish
       │  1. SSH to each ready host, read secrets.yaml, ensure student user/key
       │  2. SSH to portal, upsert lab records   ──SSH──►  sqlite (0600)
       ▼
  rodeo fleet portal status/close/release ──SSH──►  rodeo portal admin (local CLI)

  Students ──HTTPS :443──►  Caddy ──►  rodeo portal serve (127.0.0.1)
  Students ──HTTPS :8443/:30002 (+ :22 opt.)────────────────────────────►  their host
```

Key property: **the portal is passive.** It never connects to lab hosts or cloud APIs,
holds no rodeo SSH key and no cloud credentials. The laptop pushes data to it over SSH.
If the portal VM is compromised, the blast radius is the student-level credentials of
that one workshop's labs, which are ephemeral anyway.

### 4.1 Portal VM

- Provisioned through the existing `HostProvider` protocol with a reserved host id
  `portal` and an extra tag/label `rodeo-role: portal`. Default size per provider
  (AWS: `t3.small`, same Leap 16 AMI as the lab hosts, so no second AMI lookup).
  Roughly 2 US cents/hour on AWS.
- Written to `workshop.yaml` under a top-level `portal:` block (**not** `hosts[]`), so
  `fleet deploy`, `status`, `diagnose` and `retry` never fan out to it.
- `fleet deprovision` terminates it together with the labs; `--keep-portal` opts out
  (e.g. to keep the roster while re-provisioning labs).
- Bare metal / BYO: `portal.host: user@ip` skips provisioning; `portal up` just
  installs onto that machine.
- Security group: its own managed SG, tagged `rodeo-resource: portal-security-group`
  so the lab SG lookup can never match it. `443` and `80` (ACME HTTP-01 only) open to
  the world, `22` operator `/32` only, same reconcile pattern as the lab SG.
- **No rodeo private key on the portal.** The provider's provision loop plants the
  fleet-wide key on every host it creates; the portal is provisioned with
  `role: portal`, which skips that step. Only the public key is in
  `authorized_keys`, so the operator can still SSH in.

### 4.2 Software on the portal

- `rodeo` itself, installed with the existing `install.sh` at the fleet's `lab.ref`, so
  the portal runs the same version as the CLI that talks to it.
- `rodeo portal serve`: new command, stdlib `http.server.ThreadingHTTPServer` +
  `jinja2` (already a dependency) + `sqlite3`. Binds `127.0.0.1:8080`. Runs as a
  dedicated `rodeo-portal` system user under a systemd unit with the usual hardening
  (`ProtectSystem=strict`, `NoNewPrivileges`, `PrivateTmp`, state in `StateDirectory=`).
- **Caddy** in front for TLS with automatic Let's Encrypt on
  `portal-<ip-dashed>.sslip.io` (the same sslip.io trick rodeo already uses for Rancher
  `letsEncrypt`). `portal.hostname:` overrides it for a real DNS name.
- `rodeo portal admin ...`: local CLI on the portal that the laptop invokes over SSH
  (`publish`, `status`, `release`, ...). It is the only write path into the database
  besides the claim flow.

## 5. Claim flows

`portal.mode: roster | open | both` (default `both`).

### 5.1 Roster

1. `workshop.yaml` points to `portal.roster: students.csv` with columns
   `name,email[,host_id]`. A `host_id` pins a student to a lab; otherwise labs are
   assigned in order at invite time.
2. `rodeo fleet portal invite` creates one unguessable token per student
   (`secrets.token_urlsafe(24)`), stores only its SHA-256 on the portal, and writes
   `portal-invites.csv` locally (`name,email,host_id,url`) for mail-merge. The file is
   0600, and the command reminds the operator it contains credentials-equivalent links.
3. The student opens `https://<portal>/l/<token>` and sees their lab card.

### 5.2 Open (event code)

1. `rodeo fleet portal info` shows the portal URL and an event code
   (e.g. `WOLF-4821`, 8+ chars from an unambiguous alphabet), meant for a slide.
2. The student enters code + email + a 4-digit PIN they choose.
3. In one sqlite transaction the portal assigns the next free, *ready* lab and returns
   a personal link (`/l/<token>`) plus a cookie. The page tells them to bookmark it.
4. Lost link: re-enter code + email + PIN, get the same lab. The PIN stops someone
   who only knows a classmate's email from opening their lab.
5. No labs left: "No free labs, ask your instructor." Claiming can be closed
   (`portal close`) or auto-closed (`portal.close_after: 2h`).

### 5.3 Both

The roster is invited first and those labs are reserved. The remaining labs are open to
the event code. This covers "registered attendees plus walk-ins".

### 5.4 What a student sees

One card: lab id, status (`ready` / `still building, refresh in a few minutes`),
Harvester URL + `admin` + password, Rancher URL + `admin` + password (only for the
components the lab has, reusing `lab.components` logic from `fleet/access.py`), and,
if enabled, SSH: a download link for their private key plus a copy-paste
`ssh -i lab-07.key student@<ip>` line. Also a self-signed-certificate note for the lab
UIs. Pages send `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

## 6. Security model

| Threat | Mitigation |
|--------|------------|
| Portal compromise | Portal stores only student-level creds for this workshop; no cloud creds, no rodeo key, no admin web UI. Short-lived: destroyed at deprovision. Encrypted root volume where the provider supports it. |
| Guessing the event code | 8+ char code, per-IP rate limit and lockout on the claim endpoint, `portal close` to stop claiming, `portal rotate-code`. |
| Leaked personal link | Tokens stored hashed; `portal release <lab>` or `portal revoke <email>` invalidates the token. |
| Student escalates to other labs | **Student user has no sudo and cannot read `/root`.** This is mandatory while the fleet-wide rodeo key is planted in `/root/.ssh/` (section 1). `:22` is opened to the internet only when `student_ssh: true`, key-only. |
| Lab UIs reachable from the internet (`student_access: open`) | Enforced strong per-host passwords, only the component ports, short lifetime. See section 7. |
| Credentials in transit | Laptop to hosts and to portal: SSH only. Students to portal: HTTPS only (HTTP redirects, HSTS). |
| Secrets in logs | Server logs method, path template and status only; never tokens, PINs, emails or passwords. |
| Personal data (emails, GDPR) | Emails exist only in the portal DB and the local invites CSV. Deleted with the portal VM; `portal export` for the instructor's attendance list, then gone. |

Follow-up, out of scope here but worth its own ROADMAP line: replace the fleet-wide key
planted in `/root/.ssh/id_ed25519` with a per-host key for the nested hops. Once that
lands, giving students sudo on their own host becomes safe to consider.

## 7. Student network access (prerequisite, useful even without the portal)

**Decision (Andrés, 2026-09-25): students must reach their lab without knowing anything
about their network or IP.** So there is no per-student or per-venue allowlist. The lab
ports are opened to the internet for workshops that ask for it. Alternatives rejected:

- *Auto-allowlist the student's IP on claim*: breaks silently when the student's IP
  changes (mobile, VPN, another Wi-Fi), and needs the portal to hold a cloud permission.
- *Proxy all lab traffic through the portal*: VM image uploads and consoles through one
  small VM, and Rancher/Harvester misbehave behind a different hostname.

New `workshop.yaml` field under `provider:`:

```yaml
provider:
  student_access: open     # operator (default) | open
```

- `operator` (default, today's behaviour): exactly the operator `/32` on every managed
  port. Dev and test fleets stay as they are.
- `open`: `_reconcile_managed_sg_ingress` desired state becomes operator `/32` plus
  `0.0.0.0/0` on the lab UI ports (`8443`, `30002`), and on `22` only when
  `portal.student_ssh: true`. Otherwise `22` stays operator-only. Anything else is
  revoked, so switching back to `operator` and re-running provision closes the ports
  again (idempotent, testable with the existing mocked EC2 pattern). IPv4 only; the
  hosts have no public IPv6.
- `open` prints a warning at provision time naming the exposed ports and hosts.

Safeguards that come with `open`:

| Safeguard | Detail |
|-----------|--------|
| Strong passwords enforced | Before the portal publishes a lab (and in `fleet deploy` when `open`), rodeo checks the host's `secrets.yaml`: every UI admin password must be at least 16 chars with upper, lower and digit (what `secretgen.random_password` produces, about 95 bits). A weaker, hand-set password (e.g. via `rodeo set-password`) blocks that lab with a clear error instead of exposing it. |
| SSH key-only | The EC2 userdata already sets `ssh_pwauth: false` and `PermitRootLogin prohibit-password`; the student user only has a key. |
| Minimal ports | Only the ports the lab's components use (`lab.components`), never the full `MANAGED_SG_PORTS` blindly. |
| Short lifetime | Labs live for the workshop; the portal shows the teardown time and `fleet deprovision` closes everything. |
| BYO security groups untouched | With `provider.security_group_ids` set, rodeo does not change rules; `open` then only warns that the operator must open the ports. |

Accepted residual risk: the Harvester and Rancher login pages are internet-visible for
the workshop's duration and will be found by scanners. With random 95-bit passwords the
realistic exposure is an unpatched vulnerability in those products, limited by current
versions and hours-long lifetimes. This matches how hosted lab platforms operate.

Bare metal fleets have no managed SG; `student_access` is ignored there and
`fleet doctor` reports whether each host's UI ports are reachable from outside.

## 8. `workshop.yaml` additions

```yaml
portal:
  enabled: true
  mode: both                 # roster | open | both
  roster: students.csv       # roster / both
  student_ssh: false         # create a per-lab 'student' user + key, open :22 to students
  close_after: 2h            # optional: stop accepting new claims
  # instance_type: t3.small  # provider-specific override
  # hostname: labs.example.com   # instead of portal-<ip>.sslip.io
  # host: root@203.0.113.50      # BYO / bare metal: skip provisioning

  # written by provision, like hosts[]:
  # ssh: ec2-user@203.0.113.50
  # public_ip: 203.0.113.50
  # labels: {provider_id: i-0abc...}
```

`load_inventory` validates this block and fails closed on unknown `mode`, a missing
roster file, or `portal.enabled: true` on a provider fleet whose
`provider.student_access` is not `open` (students could not reach their labs).

## 9. CLI surface

| Command | Runs where | What |
|---------|-----------|------|
| `rodeo fleet provision` | laptop | Also provisions the portal VM when `portal.enabled` (`--no-portal` to skip). |
| `rodeo fleet portal up` | laptop to portal | Install rodeo + Caddy, write systemd units, start. Idempotent. |
| `rodeo fleet portal publish` | laptop to hosts, then portal | For each host: check readiness (`rodeo status --output json`), read `secrets.yaml`, ensure student user/key if enabled, upsert the lab record. Labs not ready are published as `building`. Safe to run repeatedly; run it again after `fleet retry`. |
| `rodeo fleet portal invite` | laptop | Roster tokens, writes `portal-invites.csv`. Re-running keeps existing tokens unless `--rotate`. |
| `rodeo fleet portal info` | laptop | Portal URL, event code, open/closed. |
| `rodeo fleet portal status [--output json]` | laptop to portal | Lab, state, student, claimed-at. Also shown as a `student` column in `fleet status`. |
| `rodeo fleet portal open / close / rotate-code` | laptop to portal | Claim window control. |
| `rodeo fleet portal release <lab>` / `reassign <email> <lab>` / `revoke <email>` | laptop to portal | Fix mistakes during a workshop. |
| `rodeo fleet portal export` | laptop to portal | Attendance CSV (name, email, lab). |
| `rodeo fleet deprovision` | laptop | Also terminates the portal (`--keep-portal` to keep it). |

All of them support `--output json` so the n8n plan can call them without parsing Rich
tables.

## 10. Code layout

New modules only; existing working code is touched only where noted.

| Path | Kind |
|------|------|
| `rodeo/portal/server.py`, `db.py`, `claims.py`, `templates/*.html` | new: the service on the portal VM |
| `rodeo/portal/admin.py` | new: `rodeo portal admin` local CLI |
| `rodeo/fleet/portal.py` | new: laptop-side orchestration (provision, up, publish, ...) |
| `rodeo/commands/portal_cmd.py` | new: `rodeo portal serve/admin` and `rodeo fleet portal ...` group |
| `rodeo/data/portal/` | new: systemd unit, Caddyfile template |
| `rodeo/fleet/inventory.py` | edit: parse/validate `portal:` and `provider.student_access` |
| `rodeo/providers/aws.py` | edit: `student_access: open` in SG reconcile; `role` tag for the portal instance |
| `rodeo/providers/base.py` | edit: optional `role` on `ProvisionSpec` |
| `rodeo/commands/fleet_cmd.py` | edit: wire provision/deprovision/status hooks |
| `docs/fleet.md`, `mkdocs.yml`, `ROADMAP.md` | edit: new phase F5 |

## 11. Phases

The step-by-step plan, the no-regression contract, the risks found in the current code
and the test and security gates are in [claim-portal-plan.md](claim-portal-plan.md).
Summary:

| Phase | Deliverable |
|-------|-------------|
| **F5.0** | Baseline tests pinning today's behaviour, then `provider.student_access: open` + password strength gate |
| **F5.1** | Portal service (new code only): db, claim flows, `rodeo portal serve/admin` |
| **F5.2** | Portal VM lifecycle: provision/up/deprovision, own SG, no key planting, Caddy + TLS |
| **F5.3** | `publish`, `invite` and instructor commands, `fleet status` student column |
| **F5.4** | Optional student SSH (off by default, no sudo) |
| **F5.5** | Docs, ROADMAP, CHANGELOG |

## 12. Open questions

1. **Caddy on Leap 16.** Needs checking whether it is in the Leap 16 repos. Fallback: the
   official static binary pinned by version and SHA-256.
2. **Do the workshop exercises need SSH on the KVM host at all?** If
   suse-virt/suse-edge exercises are UI-only, `student_ssh` stays `false` and F5.4 can
   be skipped.
3. **Should the instructor also get a read-only web view** (who claimed what) for
   the room screen? Currently excluded (CLI only).
4. **Lab expiry shown to students** ("this lab is destroyed at 17:00"): easy to add if
   `workshop.yaml` gains an `ends_at`, which the n8n plan also wants.
