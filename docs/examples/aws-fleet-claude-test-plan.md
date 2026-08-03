# Claude Code test plan — AWS single-host + Fleet multi-host

Executable checklist for [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
(or a human) against a laptop with AWS API access. Run steps **in order**.
Stop on the first failed check. Do not invent CLI flags. Do not skip teardown
unless the operator says so.

Related docs: [testing](testing.md), [fleet](../fleet.md), [get-started](../get-started.md).

---

## Agent rules

1. Work from a **dedicated work dir** on the laptop (not the rodeo-cli source tree
   for live deploys). Example: `~/rodeo-aws-tests`.
2. Prefer `--yes` / non-interactive flags. Never prompt the operator for secrets
   that should already exist in the environment.
3. After each numbered step, record: **PASS** / **FAIL** + one line of evidence
   (command exit code, key stdout snippet, or path written).
4. On **FAIL**: capture the failing command, stderr, and (for fleet) run
   `rodeo fleet diagnose` before asking the operator what to do.
5. Never put AWS keys in YAML. Never print passwords from `secrets.yaml` or
   access sheets.
6. Cost warning: each host is roughly `i7i.8xlarge`-class. Tear down when done.

---

## Operator fill-in (required before Part A / B)

Copy and set these once. Claude Code must refuse to start provision if any are empty.

| Variable | Example | Your value |
|----------|---------|------------|
| `AWS_REGION` | `eu-central-1` | |
| `SUBNET_ID` | `subnet-…` | |
| `SG_IDS` | `sg-…` (allow **22**, **8443**, **30002**) | |
| `WORK_DIR` | `~/rodeo-aws-tests` | |
| Fleet `count` | `2` (min for multi-host) | |

Optional:

| Variable | Default |
|----------|---------|
| Profile | `harvester` |
| Instance tier (single-host) | `recommended` → `i7i.8xlarge` |
| Marketplace | Subscribe once to [openSUSE Leap](https://aws.amazon.com/marketplace/pp/prodview-wn2xje27ui45o) |

---

## Prerequisites (laptop)

```bash
mkdir -p "$WORK_DIR" && cd "$WORK_DIR"

# Creds: ~/.aws/credentials  OR  AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY
# Confirm identity (do not print secret keys):
aws sts get-caller-identity

python3 -m pip install -U 'rodeo-cli[aws]'
# Or from a checkout: pip install -e '.[aws,dev]'
rodeo --version
```

**Pass when:** `sts` succeeds and `rodeo --version` prints a version.

---

## Part A — AWS single-host (`rodeo up --target aws`)

Goal: one EC2 KVM host, remote lab deploy, NVMe host context, SSH helpers, cloud destroy.

### A1 — Full single-host up

`--no-deploy` only seeds the lab on the laptop; it does **not** call EC2.
Capacity (offerings + `RunInstances` DryRun) runs at provision time inside this
command. If the region has no capacity, expect a **clear error** — no silent
downsize.

You need `provider.region`, `subnet_id`, and `security_group_ids` in the seeded
plan. Easiest path: seed once, edit the plan, then up.

```bash
cd "$WORK_DIR"
rodeo up --yes --profile harvester --no-deploy --dir ./single-host
# Edit ./single-host/rodeo-plan.yaml:
#   deployment_target: aws
#   provider:
#     type: aws
#     region: <AWS_REGION>
#     instance_tier: recommended
#     subnet_id: <SUBNET_ID>
#     security_group_ids: [<SG_IDS>]
#     ssh_user: ec2-user
#     volume_size_gib: 100

cd "$WORK_DIR/single-host"
rodeo up --yes --target aws --instance-tier recommended
```

Long-running (often 90–150+ min for `harvester`). Stay attached or poll logs
(`~/.rodeo/logs/…` on the **remote** host via `rodeo ssh`).

**Pass when:** command exits 0 and success output shows Harvester / Rancher URLs.
Fail-closed capacity errors at provision count as a **valid gate** (fix the
region/type, re-run) — not a product PASS until a successful deploy completes.

### A2 — Verify host context + lab health

```bash
rodeo ssh primary -- 'findmnt -n -o TARGET,SOURCE | head -50'
rodeo ssh primary -- 'lsblk -o NAME,SIZE,TYPE,MOUNTPOINT'
# From the remote lab dir (path from success output / seeded lab):
rodeo ssh primary -- 'cd /root/lab 2>/dev/null || cd ~/lab; rodeo status --output json'
```

Adjust remote lab path if the seed used a different dir. Inspect plan/vars for
Harvester disk sizing when NVMe is present (`disk_gb` **1200**, `storage.backend: nvme`).

**Pass when:**

1. Instance-store NVMe is mounted (or documented skip if type has no NVMe).
2. `rodeo status --output json` shows healthy / expected phase completion.
3. Nested guest disks live under the NVMe `image_dir` when backend is `nvme`.

### A3 — Nested SSH targets

```bash
rodeo ssh primary/rancher -- 'hostname'
# Optional: Harvester node if present in ssh targets
# rodeo ssh primary/<node> -- 'hostname'
```

**Pass when:** SSH returns 0 without password prompts.

### A4 — Tear down single-host cloud

```bash
# From the lab dir that holds the AWS plan / state on the laptop side:
rodeo destroy --cloud --yes
```

**Pass when:** EC2 instance tagged for this lab is terminated (confirm in AWS console
or `aws ec2 describe-instances`).

---

## Part B — Fleet multi-host (AWS provision + fan-out)

Goal: `count >= 2` EC2 hosts, `fleet doctor` → `deploy` → poll `status` →
`access` → `deprovision`.

Use a **new** subdirectory so Part A state does not collide.

### B0 — Write inventory

```bash
mkdir -p "$WORK_DIR/fleet-multi" && cd "$WORK_DIR/fleet-multi"

cat > workshop.yaml <<EOF
name: aws-fleet-smoke
lab:
  dir: /root/lab
  profile: harvester
  target: baremetal
  concurrency: 2
  ports:
    harvester: 8443
    rancher: 30002
defaults:
  ssh_user: ec2-user
provider:
  type: aws
  count: 2
  region: ${AWS_REGION}
  instance_tier: recommended
  # Or pin: instance_type: i7i.8xlarge
  subnet_id: ${SUBNET_ID}
  security_group_ids:
    - ${SG_IDS}
hosts: []
EOF
```

If `instance_tier` is rejected by fleet provision in your build, replace with
`instance_type: i7i.8xlarge`.

**Pass when:** file exists; `count: 2`; `hosts: []`; no credentials in the file.

### B1 — Provision hosts

```bash
cd "$WORK_DIR/fleet-multi"
rodeo fleet provision -f workshop.yaml
```

**Pass when:** exit 0; `workshop.yaml` `hosts:` has **two** entries with `ssh` /
`public_ip`; SSH to both works:

```bash
rodeo ssh student-01 -- 'uname -a'
rodeo ssh student-02 -- 'uname -a'
```

### B2 — Doctor (readiness)

```bash
rodeo fleet doctor -f workshop.yaml -j 2
```

**Pass when:** exit 0 (KVM / nested virt / tools / profile fit OK on every host).

### B3 — Deploy (async fan-out)

```bash
rodeo fleet deploy -f workshop.yaml -j 2
```

**Pass when:** exit 0 quickly; `workshop.job.yaml` exists (chmod 600); host states
are `running` or already `ok`.

### B4 — Poll status until done

Repeat until every host is `ok` or `failed` (expect 90–150+ min):

```bash
rodeo fleet status -f workshop.yaml --output json
sleep 120
```

On any `failed`:

```bash
rodeo fleet diagnose -f workshop.yaml -o "$WORK_DIR/fleet-multi/diag" --output json
rodeo fleet retry -f workshop.yaml --failed-only
```

**Pass when:** all selected hosts reach lab-complete / job `ok`, or operator accepts
documented failure after diagnose.

### B5 — Access sheet

```bash
rodeo fleet access -f workshop.yaml --output json
```

**Pass when:** JSON lists per-host URLs (Harvester / Rancher ports) using `public_ip`;
no passwords in the output.

Spot-check nested SSH:

```bash
rodeo ssh student-01/rancher -- 'hostname'
```

### B6 — Deprovision

```bash
rodeo fleet deprovision -f workshop.yaml --yes
```

**Pass when:** tagged instances terminated. Note MVP gap: `hosts[]` may still list
old IPs — edit or re-provision to refresh YAML ([fleet.md](../fleet.md)).

---

## Results log (fill as you go)

| Step | Result | Evidence |
|------|--------|----------|
| Prereqs | | |
| A1 | | |
| A2 | | |
| A3 | | |
| A4 | | |
| B0 | | |
| B1 | | |
| B2 | | |
| B3 | | |
| B4 | | |
| B5 | | |
| B6 | | |

**Overall:** PASS only if A1–A4 and B1–B6 pass.

---

## Claude Code one-shot prompt

Paste after filling the operator table:

```text
Execute docs/examples/aws-fleet-claude-test-plan.md end-to-end.
Use WORK_DIR, AWS_REGION, SUBNET_ID, SG_IDS from my message.
Follow Agent rules: stop on first FAIL, no invented flags, no printing secrets.
Record the Results log table when finished. Tear down with A4 and B6.
```
