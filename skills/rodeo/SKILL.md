---
name: rodeo
description: Deploy, operate and tear down rodeo-cli labs (Rancher, Harvester, SUSE Edge on nested KVM, on a Linux host or on AWS). Use when the user mentions rodeo, rodeo-cli, a rodeo lab or profile, or wants a Rancher or Harvester lab built for them.
---

# rodeo-cli

rodeo-cli builds hands-on labs as nested KVM VMs from a profile. It runs in one of two modes:

- **KVM host**: rodeo runs on the Linux machine that hosts the VMs (SLES 16 / Leap 16).
- **Control machine**: rodeo runs on a laptop (macOS or any Linux) and deploys to an EC2 host with `--target aws`.

Ask which mode applies before the first deploy if it is not obvious. `rodeo doctor` on the machine answers it.

The docs are the source of truth. Read the matching one before acting: `README.md`, `docs/guide-rancher.md`, `docs/guide-harvester.md`, `docs/guide-suse-edge.md`, `docs/examples/aws-macos.md`, `docs/runbook.md`, `docs/reference/plan.md`. Online: https://avaleror.github.io/rodeo-cli/

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
```

Update an existing install with `rodeo self-update`.

## Deploy

Pick the profile with `rodeo profiles` (lists bundled and custom ones) and `rodeo doctor` (recommends the largest that fits the host RAM). Then:

```bash
rodeo up --profile <profile> --yes                # on a KVM host
rodeo up --profile <profile> --target aws --yes   # from a laptop
```

For AWS, the user needs a valid session first: `aws login`, then `aws sts get-caller-identity` to confirm the account. `aws login` opens a browser, so the user runs it, not the agent.

A deploy takes 20 minutes or more. `rodeo up` wraps itself in tmux (`rodeo-<profile>`), so it survives a dropped terminal. If your shell tool has a timeout or kills processes when a call ends, do not run `rodeo up` yourself: give the user the exact command to run in their terminal, then follow progress with `rodeo status`. Never pass `--no-tmux` for a long deploy.

A failed deploy resumes from the failed phase: re-run the same `rodeo up` command, or `rodeo deploy --from <phase>`. Phases: `kvm_host`, `vms`, `boot` or `pxe_server` and `cluster`, `rancher`, `elemental`, `apply`, `finalise`.

## Operate

Run lab commands from the lab directory (`~/rodeo-labs/<profile>` by default) or pass `--config-dir <lab dir>`.

| Need | Command |
|---|---|
| URLs, credentials, VM states, phase progress | `rodeo status` |
| Preview what a deploy would change | `rodeo plan` |
| Shell on a lab VM | `rodeo ssh <vm>` |
| VM serial log, support bundle | `rodeo logs <vm>`, `rodeo logs --bundle` |
| Stop or start the lab | `rodeo stop`, `rodeo start` |
| Restart one VM | `rodeo restart <vm>` |
| Rotate admin passwords | `rodeo set-password` |

Rancher UI: `https://<host-ip>:30002`, user `admin`, self-signed certificate. The password is `rancher_admin_password` in `~/.rodeo/secrets.yaml`. Do not print passwords in the conversation unless the user asks for them.

## AWS rules

- Every cloud host terminates itself `provider.ttl_hours` after launch (default 6, max 168). Tell the user the expiry when a lab comes up. To change it, set `provider.ttl_hours` in `rodeo-plan.yaml` before `rodeo up`.
- The security group admits only the public IP of the last `rodeo up`. If the UI or SSH times out after a network change, re-run the same `rodeo up` command: it moves the rule and skips finished phases.
- In a shared AWS account, the key pair `rodeo` may belong to someone else. Never delete a key pair, instance or security group the user did not create. Use a personal key pair name instead, as described in `docs/examples/aws-macos.md` ("Key pair already exists in a shared account").
- Capacity or region errors: see `docs/runbook.md` ("AWS: login, region and capacity errors"). rodeo never downsizes the instance on its own.

## Destructive commands

Run these only when the user asked for that action in the current conversation, and confirm the lab directory first:

- `rodeo destroy --cloud --yes --config-dir <lab dir>`: terminates the EC2 host.
- `rodeo clean`: deletes lab VMs, disks and state. `rodeo clean --all --yes --secrets` resets the whole host.

## When something fails

Check `docs/runbook.md` for the symptom first. If it is not there, collect `rodeo logs --bundle` and report it at https://github.com/avaleror/rodeo-cli/issues with the steps to reproduce.
